"""Adapter ports for the Agent Orchestration context.

The two things every LLM-calling node depends on — a way to call a model, and a way to persist a
run — are protocols here, not concrete classes, for the same reason the rest of this codebase uses
ports everywhere (see ``apps/api``'s ``retrieval`` context injecting ``EmbeddingGenerator`` around
BGE-M3): the application layer and the graph nodes stay testable against fakes, and the real
LiteLLM-gateway / real-Postgres adapters are a swap behind the port, not a redesign.

:class:`LlmClient` is the single most important port in the service. Every agent node that reasons
with a model calls ``LlmClient.complete`` — never LiteLLM, never a provider SDK directly. This is
what makes the whole graph runnable and testable with a fake client today, before any API key
exists, and makes swapping the underlying model a gateway-config change rather than a code change.
"""

from __future__ import annotations

from typing import Protocol

from agent_orchestrator.domain.agents import HitlDecision, RunStatus
from agent_orchestrator.domain.entities import (
    AgentRun,
    HitlInterrupt,
    Message,
    ModelResponse,
    PromptMessage,
    RetrievedChunk,
)


class LlmClient(Protocol):
    """The model-access port — the only thing an agent node calls to reason with a model.

    A single method: turn a list of messages into a :class:`ModelResponse`. ``model`` is a LiteLLM
    model *alias* (a name from ``config/litellm.yaml``'s ``model_list``), not a raw provider model
    id — routing that alias to a concrete model, applying rate limits and cost tracking, and
    supplying the provider credential are all the gateway's job, invisible to the caller.

    The real adapter (``infrastructure/llm_client.py``) raises ``LlmProviderNotConfiguredError`` if
    no ``AGENT_LLM_API_KEY`` is present; a fake adapter (used in every test) returns a canned
    ``ModelResponse`` with no network call. A node written against this port behaves identically
    against either — that is the entire point.
    """

    async def complete(
        self, *, messages: list[PromptMessage], model: str, max_tokens: int = 1024
    ) -> ModelResponse: ...


class RunRepository(Protocol):
    """Persistence for ``agent.runs`` — create a run, advance its status, read it back.

    Owned by this context (the ``agent`` schema is this service's), so its concrete adapter writes
    through SQLAlchemy the same way ``apps/api``'s owned-table repositories do. All access is
    engagement-scoped by the RLS policy on ``agent.runs`` — this port never adds a ``WHERE
    engagement_id`` clause of its own, the database enforces it.
    """

    async def create_run(self, run: AgentRun) -> AgentRun: ...

    async def get_run(self, run_id: str) -> AgentRun | None: ...

    async def set_status(self, run_id: str, status: RunStatus) -> None: ...

    async def list_runs(self, engagement_id: str) -> list[AgentRun]: ...


class ApiSearchClient(Protocol):
    """Real evidence retrieval against apps/api's own search endpoints — the ``hybrid_search``
    tool (``application/tool_registry.py``). A specialist node calls this, not the LLM, to gather
    real passages before it ever drafts anything — the LLM only ever sees what this returns."""

    async def semantic_search(
        self, *, engagement_id: str, query: str, limit: int = 5
    ) -> list[RetrievedChunk]: ...


class FindingWriter(Protocol):
    """Writes an agent's evidence-grounded draft back into apps/api's reporting context as a real
    ``reporting.findings`` row, citing the real chunks its evidence came from (the
    ``submit_finding_draft`` tool) — the write half of the RAG loop. The finding is created as a
    draft; apps/api's existing, unmodified human sign-off gate (an Auditor or Fraud Analyst
    confirming/rejecting) is what makes it count, exactly as it is for a manually-authored
    finding.

    Takes the full :class:`RetrievedChunk` list (not bare ids) so the adapter can attach each
    citation's own snippet as ``citation_text`` — apps/api's ``AttachEvidenceRequest`` requires a
    non-empty citation, and only the chunk itself has text worth quoting there.
    """

    async def submit_finding_draft(
        self,
        *,
        engagement_id: str,
        title: str,
        description: str,
        severity: str,
        evidence: list[RetrievedChunk],
    ) -> str: ...


class ContextReader(Protocol):
    """Read-only context lookups against apps/api's already-computed signals — the
    ``get_anomaly_cluster``/``get_risk_score`` and ``graph_traverse`` tools. Plain ``dict`` results
    (not typed entities like :class:`RetrievedChunk`): these feed a prompt as contextual grounding,
    not a citation graph an evidence chain is built from, so the extra type ceremony isn't pulling
    its weight the way it does for evidence chunks."""

    async def list_anomalies(self, *, engagement_id: str) -> list[dict[str, object]]: ...

    async def list_risk_scores(self, *, engagement_id: str) -> list[dict[str, object]]: ...

    async def list_vendors(self, *, engagement_id: str) -> list[dict[str, object]]: ...


class RagApiClient(ApiSearchClient, FindingWriter, ContextReader, Protocol):
    """The combined RAG tool port :class:`~agent_orchestrator.application.nodes.AgentNodes`
    actually depends on. One adapter (``infrastructure/api_client.py``'s ``HttpApiClient``)
    implements all three faces; nodes depend on this single combined shape rather than juggling
    three separate constructor parameters for what is, in practice, always the same apps/api HTTP
    client bound to the same caller's bearer token."""


class HitlRepository(Protocol):
    """Persistence for ``agent.hitl_interrupts``.

    ``open_interrupt`` records a pending human checkpoint when the graph pauses; ``resolve``
    records the reviewer's decision. The append-then-resolve shape (rather than deleting/replacing)
    keeps every human decision durable for the audit trail, the same guarantee
    ``apps/api``'s audit_trail context already provides.
    """

    async def open_interrupt(self, interrupt: HitlInterrupt) -> HitlInterrupt: ...

    async def get_interrupt(self, interrupt_id: str) -> HitlInterrupt | None: ...

    async def resolve(
        self, interrupt_id: str, *, decision: HitlDecision, reviewer_id: str, reason: str | None
    ) -> HitlInterrupt: ...

    async def list_open_for_run(self, run_id: str) -> list[HitlInterrupt]: ...

    async def list_all_for_engagement(self, engagement_id: str) -> list[HitlInterrupt]:
        """Every interrupt for the engagement, open or resolved — what ``EvaluationMetrics``
        aggregates over (``application/evaluation.py``). Distinct from ``list_open_for_run``,
        which a reviewing UI polls for one run's *pending* decision; this is read-only analytics
        over the full, already-recorded history."""
        ...


class CopilotActionsApiClient(Protocol):
    """The six direct-action tool calls AI Copilot's chat router may invoke
    (``application/copilot_actions.py``) — a third, independent face of apps/api access alongside
    :class:`ApiSearchClient`/:class:`FindingWriter`/:class:`ContextReader`, since these are
    dispatched directly from a chat turn, outside the investigation graph entirely, not by a
    specialist node reasoning over a plan."""

    async def import_transactions(
        self, *, engagement_id: str, transactions: list[dict[str, object]]
    ) -> list[dict[str, object]]: ...

    async def run_risk_scan(self, *, engagement_id: str) -> list[dict[str, object]]: ...

    async def compute_risk_scores(self, *, engagement_id: str) -> list[dict[str, object]]: ...

    async def resolve_vendors(self, *, engagement_id: str) -> dict[str, object]: ...

    async def generate_report(self, *, engagement_id: str) -> dict[str, object]: ...

    async def generate_embeddings(
        self, *, engagement_id: str, document_id: str
    ) -> dict[str, object]: ...


class MessageRepository(Protocol):
    """Persistence for ``agent.messages`` — one user's private AI Copilot thread on one engagement.

    Never updated or deleted once created (the same append-only discipline every other governance
    record in this platform follows) — a conversation only ever grows.
    """

    async def create_message(self, message: Message) -> Message: ...

    async def get_message(self, message_id: str) -> Message | None: ...

    async def list_messages(self, *, engagement_id: str, user_id: str) -> list[Message]: ...
