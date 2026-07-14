"""FastAPI dependencies wiring identity, persistence, and the orchestration service into the
request lifecycle (Phase 3 §1) — the only layer in this service allowed to import FastAPI.

**Why this resolves identity via raw SQL against ``identity.*`` instead of importing
``apps/api``'s identity context**: the two services are separate deployables (ADR-001) that never
share a Python package, but they share one physical database (Phase 4 §1). Reading another
context's tables directly through a raw, parameterized query — never through that context's ORM
class — is the exact convention ``apps/api`` itself already uses across bounded contexts
(``risk``'s ``AuditTrailRecorder`` and ``Neo4jVendorCentralitySource``, Increment 10); this module
applies the same rule across the service boundary.

**What this service deliberately does NOT do**: JIT-provision a new ``identity.users`` row for an
unrecognized token subject. That provisioning is ``apps/api``'s identity context's job
(Increment 02) — a caller must already have signed into the main application at least once. A
token whose subject has no matching ``identity.users`` row is rejected here with a clear 401
rather than silently creating a second, divergent user-provisioning path.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.application.copilot_actions import build_copilot_actions
from agent_orchestrator.application.copilot_chat_service import CopilotChatService
from agent_orchestrator.application.evaluation import EvaluationService
from agent_orchestrator.application.orchestrator import OrchestrationService
from agent_orchestrator.domain.ports import CopilotActionsApiClient, LlmClient, RagApiClient
from agent_orchestrator.infrastructure.api_client import HttpApiClient
from agent_orchestrator.infrastructure.gateway_config import load_gateway_config
from agent_orchestrator.infrastructure.llm_client import LiteLlmGatewayClient
from agent_orchestrator.infrastructure.repositories import (
    PostgresHitlRepository,
    PostgresMessageRepository,
    PostgresRunRepository,
)
from agent_orchestrator.shared.auth import AuthenticatedUser, get_bearer_token, get_current_user
from agent_orchestrator.shared.database import get_db_session, set_rls_user_context
from agent_orchestrator.shared.errors import AuthenticationError, AuthorizationError
from agent_orchestrator.shared.settings import Settings, get_settings


async def get_current_db_user_id(
    auth_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> str:
    """Resolves the validated token subject to an existing ``identity.users`` id, then binds the
    Row-Level Security context for every subsequent query in this request's transaction (Phase 4
    §12) — the same ``set_rls_user_context`` call, against the same session variable, every
    engagement-scoped table in the platform relies on.
    """
    result = await session.execute(
        text("SELECT id FROM identity.users WHERE entra_object_id = :oid"),
        {"oid": auth_user.subject},
    )
    user_id = result.scalar_one_or_none()
    if user_id is None:
        raise AuthenticationError(
            "No local identity found for this token. Sign in to the main AuditMind AI application "
            "at least once before starting an agent run."
        )
    user_id_str = str(user_id)
    await set_rls_user_context(session, user_id=user_id_str)
    return user_id_str


def require_engagement_member(
    *allowed_roles: str,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """FastAPI dependency factory: re-checks engagement membership against the database on every
    request (Phase 11 §4 — engagement scope is never trusted from the JWT). Relies on FastAPI's
    path-parameter injection — the route this is used on must declare ``{engagement_id}``.

    Usage: ``Depends(require_engagement_member())`` for "any member", or
    ``Depends(require_engagement_member("Auditor", "FraudAnalyst"))`` to additionally restrict by
    role — the same two roles ``apps/api``'s finding sign-off gate restricts to (Increment 04),
    since an agent run's HITL approval is the identical governance act (``domain/agents.py``'s
    ``HitlDecision`` docstring).
    """

    async def _check(
        engagement_id: str,
        user_id: str = Depends(get_current_db_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> str:
        result = await session.execute(
            text(
                "SELECT role FROM identity.engagement_members "
                "WHERE engagement_id = :engagement_id AND user_id = :user_id"
            ),
            {"engagement_id": engagement_id, "user_id": user_id},
        )
        role = result.scalar_one_or_none()
        if role is None:
            raise AuthorizationError(f"You are not a member of engagement {engagement_id}.")
        if allowed_roles and role not in allowed_roles:
            required = ", ".join(sorted(allowed_roles))
            raise AuthorizationError(f"This action requires one of these roles: {required}.")
        return str(role)

    return _check


def get_run_repository(
    session: AsyncSession = Depends(get_db_session),
) -> PostgresRunRepository:
    return PostgresRunRepository(session)


def get_hitl_repository(
    session: AsyncSession = Depends(get_db_session),
) -> PostgresHitlRepository:
    return PostgresHitlRepository(session)


def get_message_repository(
    session: AsyncSession = Depends(get_db_session),
) -> PostgresMessageRepository:
    return PostgresMessageRepository(session)


def get_api_client(
    bearer_token: str = Depends(get_bearer_token),
    settings: Settings = Depends(get_settings),
) -> RagApiClient:
    """The RAG tool client, bound to the *caller's own* bearer token — apps/api sees exactly the
    identity that started this run, and its RLS/engagement-membership checks enforce access there
    the same way they would for a browser request. This service never mints or holds a service
    credential of its own for these calls."""
    return HttpApiClient(base_url=settings.core_api_base_url, bearer_token=bearer_token)


def get_copilot_actions_api_client(
    bearer_token: str = Depends(get_bearer_token),
    settings: Settings = Depends(get_settings),
) -> CopilotActionsApiClient:
    """The same ``HttpApiClient`` adapter as ``get_api_client``, under the narrower port AI
    Copilot's direct actions actually need — its own dependency (rather than reusing
    ``get_api_client``'s ``RagApiClient``-typed return) so the port a caller depends on matches
    exactly the six methods it uses, not the graph's separate RAG tool surface. Same bearer-token
    forwarding, same "this service mints no credential of its own" model either way.
    """
    return HttpApiClient(base_url=settings.core_api_base_url, bearer_token=bearer_token)


def get_llm_client(settings: Settings = Depends(get_settings)) -> LlmClient:
    """The real LiteLLM gateway adapter — its own dependency (not inlined into
    ``get_orchestration_service``) specifically so tests can override *only* this piece with a
    fake and still exercise the real request-scoped session/repositories underneath it (Increment
    12's HTTP integration tests do exactly this — no ``ANTHROPIC_API_KEY`` exists in this
    environment, the same "genuinely unverifiable without a key" boundary the increment doc names).
    """
    router_config = load_gateway_config(settings.litellm_config_path)
    return LiteLlmGatewayClient(router_config=router_config)


# Process-wide, not request-scoped: a checkpoint written by `start_run` (one request) must still
# be there when `resume_after_review` (a *later*, separate request) looks it up by thread_id. A
# fresh `InMemorySaver()` built per-request — the first version of this dependency — silently lost
# every checkpoint the instant the request that wrote it finished, so `resume_after_review` always
# resumed into an empty state instead of the paused run (confirmed by hitting exactly that failure,
# a `KeyError` on the Planner's first read of `state["task"]`, against the real HTTP stack). This
# module-level singleton is the smallest fix that makes the HITL pause/resume flow survive across
# requests within one running process; it is still not durable across a process restart — that gap
# is the genuinely deferred one (`AsyncPostgresSaver`, Increment 12's doc §"deferred").
_checkpointer = InMemorySaver()


def get_orchestration_service(
    run_repository: PostgresRunRepository = Depends(get_run_repository),
    hitl_repository: PostgresHitlRepository = Depends(get_hitl_repository),
    llm_client: LlmClient = Depends(get_llm_client),
    api_client: RagApiClient = Depends(get_api_client),
    settings: Settings = Depends(get_settings),
) -> OrchestrationService:
    """Builds the request-scoped :class:`OrchestrationService`, sharing the process-wide
    checkpointer (``_checkpointer`` above) so a run started in one request can be resumed from a
    later one — everything else (the LLM client, the RAG tool client, the repositories, the
    graph's node behavior) is still freshly constructed per request, matching how ``apps/api``'s
    services are built.
    """
    return OrchestrationService(
        llm_client=llm_client,
        api_client=api_client,
        run_repository=run_repository,
        hitl_repository=hitl_repository,
        checkpointer=_checkpointer,
        default_model=settings.default_model,
        max_replans=settings.max_replans,
    )


def get_copilot_chat_service(
    llm_client: LlmClient = Depends(get_llm_client),
    message_repository: PostgresMessageRepository = Depends(get_message_repository),
    hitl_repository: PostgresHitlRepository = Depends(get_hitl_repository),
    orchestration_service: OrchestrationService = Depends(get_orchestration_service),
    copilot_actions_api_client: CopilotActionsApiClient = Depends(get_copilot_actions_api_client),
    settings: Settings = Depends(get_settings),
) -> CopilotChatService:
    """Builds the request-scoped :class:`CopilotChatService` — reuses the same
    :func:`get_orchestration_service` an "investigate" hand-off drives, so a chat-started
    investigation shares the identical process-wide checkpointer every other run does; nothing
    about the investigation pipeline is duplicated for chat.
    """
    return CopilotChatService(
        llm_client=llm_client,
        message_repository=message_repository,
        hitl_repository=hitl_repository,
        orchestration_service=orchestration_service,
        default_model=settings.default_model,
        actions=build_copilot_actions(copilot_actions_api_client),
    )


def get_evaluation_service(
    run_repository: PostgresRunRepository = Depends(get_run_repository),
    hitl_repository: PostgresHitlRepository = Depends(get_hitl_repository),
) -> EvaluationService:
    return EvaluationService(run_repository=run_repository, hitl_repository=hitl_repository)
