"""Composition root for the Agent Orchestration service (ADR-001's separate deployable).

This module's only job is wiring settings, logging, exception handlers, and routes together (Phase
3 §1) — it contains no business logic of its own, the same restraint ``apps/api``'s ``main.py``
holds itself to.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from agent_orchestrator.application.copilot_chat_service import CopilotChatService
from agent_orchestrator.application.evaluation import EvaluationService
from agent_orchestrator.application.orchestrator import OrchestrationService
from agent_orchestrator.domain.agents import HitlDecision
from agent_orchestrator.infrastructure.repositories import (
    PostgresHitlRepository,
    PostgresRunRepository,
)
from agent_orchestrator.interface.dependencies import (
    get_copilot_chat_service,
    get_current_db_user_id,
    get_evaluation_service,
    get_hitl_repository,
    get_orchestration_service,
    get_run_repository,
    require_engagement_member,
)
from agent_orchestrator.interface.schemas import (
    EvaluationMetricsResponse,
    HitlInterruptResponse,
    MessageResponse,
    ResolveCheckpointRequest,
    ResolveHitlRequest,
    RunResponse,
    SendMessageRequest,
    StartRunRequest,
)
from agent_orchestrator.shared.auth import AuthenticatedUser, JWKSClient, get_current_user
from agent_orchestrator.shared.database import get_engine
from agent_orchestrator.shared.errors import (
    AgentOrchestratorError,
    NotFoundError,
    ValidationError,
    agent_orchestrator_error_handler,
    unhandled_exception_handler,
)
from agent_orchestrator.shared.logging import configure_logging, get_logger
from agent_orchestrator.shared.settings import get_settings

logger = get_logger(__name__)

# The same two roles apps/api's finding sign-off gate restricts to (Increment 04) — starting a run
# is the authoring act, resolving a HITL interrupt is the sign-off act, and both governance acts
# are held to the identical role matrix (domain/agents.py's HitlDecision docstring).
_CAN_START_RUNS = ("Auditor", "FraudAnalyst", "ComplianceManager")
_CAN_RESOLVE_HITL = ("Auditor", "FraudAnalyst")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process startup/shutdown. Runs once per process, not per request or per test."""
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.jwks_client = JWKSClient(jwks_uri=settings.entra_jwks_uri)
    logger.info(
        "startup_complete",
        environment=settings.environment,
        llm_provider_configured=settings.llm_provider_configured,
    )
    yield
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    """Builds the FastAPI application. A function, not a module-level side effect, so tests can
    construct independent app instances (Phase 3 §1's own convention in ``apps/api``)."""
    app = FastAPI(
        title="AuditMind AI — Agent Orchestration",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_exception_handler(AgentOrchestratorError, agent_orchestrator_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.middleware("http")
    async def bind_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Binds a trace id to every log line for this request and echoes it back in the response
        header (Phase 10 §1) — the same contract ``apps/api`` establishes, so a trace id a caller
        sees from either service is the same kind of thing."""
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id, path=request.url.path)
        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        return response

    @app.get("/healthz", tags=["health"])
    async def liveness() -> dict[str, str]:
        """Liveness probe (Phase 12 §13): the process is up. Checks nothing else."""
        return {"status": "alive"}

    @app.get("/readyz", tags=["health"])
    async def readiness() -> JSONResponse:
        """Readiness probe (Phase 12 §13): verifies the database is actually reachable."""
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("readiness_check_failed", error=str(exc))
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.get("/v1/me", tags=["identity"])
    async def whoami(user: AuthenticatedUser = Depends(get_current_user)) -> dict[str, object]:
        """Returns the caller's validated identity — an end-to-end smoke test for the auth
        middleware, mirroring ``apps/api``'s identical endpoint."""
        return {"subject": user.subject, "roles": sorted(user.roles), "tenant_id": user.tenant_id}

    @app.post("/v1/engagements/{engagement_id}/agent-runs", tags=["agent-runs"], status_code=201)
    async def start_run(
        engagement_id: str,
        body: StartRunRequest,
        user_id: str = Depends(get_current_db_user_id),
        _membership: str = Depends(require_engagement_member(*_CAN_START_RUNS)),
        orchestration_service: OrchestrationService = Depends(get_orchestration_service),
    ) -> RunResponse:
        """Starts a new agent run (Phase 5 §1) and drives it to its first stopping point — the
        HITL interrupt in a configured environment, or a typed 503 if no model provider is
        configured (see ``application/orchestrator.py``)."""
        run = await orchestration_service.start_run(
            engagement_id=engagement_id,
            use_case=body.use_case,
            task=body.task,
            initiated_by=user_id,
        )
        return RunResponse.from_entity(run)

    @app.get("/v1/engagements/{engagement_id}/agent-runs", tags=["agent-runs"])
    async def list_runs(
        engagement_id: str,
        _membership: str = Depends(require_engagement_member()),
        run_repository: PostgresRunRepository = Depends(get_run_repository),
    ) -> list[RunResponse]:
        """Lists runs for the engagement — RLS scopes the result to the caller's memberships, the
        explicit ``engagement_id`` filter narrows it further to this path's engagement."""
        runs = await run_repository.list_runs(engagement_id)
        return [RunResponse.from_entity(run) for run in runs]

    @app.get("/v1/engagements/{engagement_id}/agent-runs/{run_id}", tags=["agent-runs"])
    async def get_run(
        engagement_id: str,
        run_id: str,
        _membership: str = Depends(require_engagement_member()),
        run_repository: PostgresRunRepository = Depends(get_run_repository),
    ) -> RunResponse:
        run = await run_repository.get_run(run_id)
        if run is None or run.engagement_id != engagement_id:
            raise NotFoundError(f"No agent run {run_id} on engagement {engagement_id}.")
        return RunResponse.from_entity(run)

    @app.get(
        "/v1/engagements/{engagement_id}/agent-runs/{run_id}/hitl-interrupts", tags=["agent-runs"]
    )
    async def list_open_interrupts(
        engagement_id: str,
        run_id: str,
        _membership: str = Depends(require_engagement_member()),
        hitl_repository: PostgresHitlRepository = Depends(get_hitl_repository),
    ) -> list[HitlInterruptResponse]:
        """Lists a run's open (unresolved) human-review checkpoints (Phase 5 §15) — what a
        reviewing UI polls to find the pending decision to render."""
        interrupts = await hitl_repository.list_open_for_run(run_id)
        return [
            HitlInterruptResponse.from_entity(i)
            for i in interrupts
            if i.engagement_id == engagement_id
        ]

    @app.post(
        "/v1/engagements/{engagement_id}/agent-runs/{run_id}"
        "/hitl-interrupts/{interrupt_id}/resolve",
        tags=["agent-runs"],
    )
    async def resolve_hitl_interrupt(
        engagement_id: str,
        run_id: str,
        interrupt_id: str,
        body: ResolveHitlRequest,
        user_id: str = Depends(get_current_db_user_id),
        _membership: str = Depends(require_engagement_member(*_CAN_RESOLVE_HITL)),
        hitl_repository: PostgresHitlRepository = Depends(get_hitl_repository),
        orchestration_service: OrchestrationService = Depends(get_orchestration_service),
    ) -> HitlInterruptResponse:
        """Resolves the human-in-the-loop gate (Phase 5 §15) — the identical governance act
        ``apps/api``'s finding confirm/reject endpoints perform (Increment 04), applied to an
        agent run's draft instead of a manually-authored finding. Records the reviewer's decision
        first (durable regardless of what happens next), then resumes the graph from its
        checkpoint with that decision."""
        interrupt = await hitl_repository.get_interrupt(interrupt_id)
        belongs_to_run = interrupt is not None and interrupt.run_id == run_id
        belongs_to_engagement = interrupt is not None and interrupt.engagement_id == engagement_id
        if not (belongs_to_run and belongs_to_engagement):
            raise NotFoundError(f"No open HITL interrupt {interrupt_id} on run {run_id}.")
        if body.decision != HitlDecision.APPROVE and not body.reason:
            raise ValidationError("A reject or edit decision requires a documented reason.")

        resolved = await hitl_repository.resolve(
            interrupt_id,
            decision=body.decision,
            reviewer_id=user_id,
            reason=body.reason,
        )
        await orchestration_service.resume_after_review(run_id=run_id, decision=body.decision)
        return HitlInterruptResponse.from_entity(resolved)

    @app.post(
        "/v1/engagements/{engagement_id}/copilot/messages", tags=["copilot"], status_code=201
    )
    async def send_copilot_message(
        engagement_id: str,
        body: SendMessageRequest,
        user_id: str = Depends(get_current_db_user_id),
        # Same role gate as starting a run directly (`_CAN_START_RUNS`): a chat message may itself
        # trigger a real investigation or a data-mutating direct action (import transactions, run
        # a scan, generate a report, ...), so it needs the same authoring rights, not the broader
        # "any member" a read-only question alone would technically need. CAE cannot chat under
        # this rule — a real, deliberate restriction, not an oversight (see the increment doc).
        _membership: str = Depends(require_engagement_member(*_CAN_START_RUNS)),
        copilot_chat_service: CopilotChatService = Depends(get_copilot_chat_service),
    ) -> list[MessageResponse]:
        """Sends one chat turn to AI Copilot — answers directly, invokes a direct action, or hands
        off to a real investigation, per ``CopilotChatService.send_message``'s routing."""
        messages = await copilot_chat_service.send_message(
            engagement_id=engagement_id, user_id=user_id, content=body.content
        )
        return [MessageResponse.from_entity(m) for m in messages]

    @app.get("/v1/engagements/{engagement_id}/copilot/messages", tags=["copilot"])
    async def list_copilot_messages(
        engagement_id: str,
        user_id: str = Depends(get_current_db_user_id),
        _membership: str = Depends(require_engagement_member()),
        copilot_chat_service: CopilotChatService = Depends(get_copilot_chat_service),
    ) -> list[MessageResponse]:
        """The calling user's own private thread for this engagement — RLS (``user_id =
        current_setting(...)``) means this is never another teammate's conversation."""
        messages = await copilot_chat_service.list_messages(
            engagement_id=engagement_id, user_id=user_id
        )
        return [MessageResponse.from_entity(m) for m in messages]

    @app.post(
        "/v1/engagements/{engagement_id}/copilot/messages/{message_id}/resolve",
        tags=["copilot"],
    )
    async def resolve_copilot_checkpoint(
        engagement_id: str,
        message_id: str,
        body: ResolveCheckpointRequest,
        user_id: str = Depends(get_current_db_user_id),
        _membership: str = Depends(require_engagement_member(*_CAN_RESOLVE_HITL)),
        copilot_chat_service: CopilotChatService = Depends(get_copilot_chat_service),
    ) -> MessageResponse:
        """Resolves the HITL checkpoint an earlier chat message handed off to — the same
        governance act (and the same role gate) as the standalone HITL resolve endpoint above,
        reached from the chat thread instead of a separate run-detail view."""
        resolution = await copilot_chat_service.resolve_checkpoint(
            engagement_id=engagement_id,
            user_id=user_id,
            message_id=message_id,
            decision=body.decision,
            reason=body.reason,
        )
        return MessageResponse.from_entity(resolution)

    @app.get("/v1/engagements/{engagement_id}/evaluation/metrics", tags=["evaluation"])
    async def get_evaluation_metrics(
        engagement_id: str,
        _membership: str = Depends(require_engagement_member()),
        evaluation_service: EvaluationService = Depends(get_evaluation_service),
    ) -> EvaluationMetricsResponse:
        """Real aggregate analytics over this engagement's agent runs and HITL decisions — run
        outcome distribution overall and per use case, plus how often a human reviewer approves
        vs. rejects vs. edits an agent's draft (see ``application/evaluation.py``'s docstring for
        why this is not a golden-dataset grading harness)."""
        metrics = await evaluation_service.compute_metrics(engagement_id)
        return EvaluationMetricsResponse.from_entity(metrics)

    return app


app = create_app()
