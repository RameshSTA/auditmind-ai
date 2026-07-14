"""Framework-free domain entities for run and human-review persistence.

These are frozen dataclasses (the same immutable-entity convention ``apps/api``'s contexts use for
``Finding`` / ``Anomaly`` / ``Document``) mapping one-to-one onto the ``agent.runs`` and
``agent.hitl_interrupts`` tables. ``agent.checkpoints`` has no entity here on purpose: its rows are
LangGraph's own serialized checkpoint blobs, written and read exclusively by the LangGraph Postgres
checkpointer, never constructed or interpreted by this service's application code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_orchestrator.domain.agents import HitlDecision, MessageRole, MessageType, RunStatus


@dataclass(frozen=True)
class AgentRun:
    """One LangGraph invocation (``agent.runs``).

    ``use_case`` names the task shape (e.g. ``"control_test"``, ``"fraud_triage"``) the planner
    decomposes. ``status`` tracks the run through the lifecycle in
    :class:`~agent_orchestrator.domain.agents.RunStatus`.
    """

    id: str
    engagement_id: str
    use_case: str
    status: RunStatus
    initiated_by: str
    task: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class HitlInterrupt:
    """One human checkpoint on a run (``agent.hitl_interrupts``).

    Created ``pending`` (``decision`` is ``None``) when the graph pauses at the interrupt node, then
    resolved when a reviewer approves/rejects/edits — the reviewer id and reason are recorded at
    that point, mirroring exactly how ``apps/api``'s reporting context records ``reviewed_by`` and
    ``disposition_reason`` on a finding. ``decision is None`` distinguishes an open interrupt from a
    resolved one without a separate status column.

    ``engagement_id`` is carried on the entity (not only the table) because it is load-bearing for
    RLS: the interrupt row must be scoped to the run's engagement, and the orchestrator that opens
    the interrupt is the one that knows it. Keeping it on the entity avoids a fragile
    persistence-only side channel and makes the isolation requirement explicit in the type.
    """

    id: str
    run_id: str
    engagement_id: str
    step_name: str
    decision: HitlDecision | None = None
    reviewer_id: str | None = None
    reason: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class UseCaseOutcomeCounts:
    """How one ``use_case``'s runs broke down by terminal/in-flight status — one row per use case
    in :class:`EvaluationMetrics`'s breakdown, the same "group by the field that actually varies"
    shape a per-severity breakdown already uses elsewhere in this platform."""

    use_case: str
    status_counts: dict[str, int]


@dataclass(frozen=True)
class ApprovalRateEstimate:
    """A non-parametric bootstrap 95% confidence interval on the HITL approval rate
    (``application/bootstrap_ci.py``) — the in-app counterpart to
    ``analytics/notebooks/hitl_agreement_analysis.ipynb``'s methodology. A wide interval at small
    sample sizes is the statistically correct output, not a bug to hide."""

    point_estimate: float
    ci_low: float
    ci_high: float
    sample_size: int


@dataclass(frozen=True)
class EvaluationMetrics:
    """Real, already-recorded agent-run and HITL-decision analytics for one engagement — not a
    golden-dataset grading framework (there is no labeled answer key to grade against yet).
    Every field here is a straight aggregation over ``agent.runs`` and ``agent.hitl_interrupts``
    rows this service already writes in the normal course of starting runs and resolving HITL
    gates (``application/orchestrator.py``); nothing here is computed from a fabricated or assumed
    ground truth.
    """

    total_runs: int
    run_status_counts: dict[str, int]
    use_case_breakdown: tuple[UseCaseOutcomeCounts, ...]
    total_interrupts: int
    open_interrupts: int
    resolved_interrupts: int
    decision_counts: dict[str, int]
    recent_reject_edit_reasons: tuple[HitlInterrupt, ...]
    # None when there are zero resolved interrupts — nothing to estimate a rate over yet.
    approval_rate: ApprovalRateEstimate | None = None


@dataclass(frozen=True)
class ModelResponse:
    """The result of one gateway model call (the :class:`LlmClient` port's return type).

    Deliberately minimal: the generated ``text`` plus the ``model`` alias that produced it (for the
    checkpointed audit trail — which model saw which context) and coarse token counts for the
    per-run cost accounting the gateway centralizes. It is *not* the raw provider response object —
    keeping the port's return type provider-agnostic is the whole point of routing through a
    gateway: a node reasons over ``ModelResponse``, never over a provider-specific payload.
    """

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class PromptMessage:
    """One message in a gateway request (system / user / assistant).

    A plain role+content pair rather than a provider- or LangChain-specific message class, for the
    same provider-agnostic reason as :class:`ModelResponse`. The gateway adapter translates a list
    of these into whatever wire format LiteLLM expects.
    """

    role: str
    content: str


@dataclass(frozen=True)
class RetrievedChunk:
    """One evidence passage returned by apps/api's semantic search — the unit real RAG grounds an
    agent's draft in (the ``hybrid_search`` tool, ``application/tool_registry.py``).

    ``chunk_id``/``document_id`` are real primary keys in apps/api's ``ingestion`` schema — a
    citation built from one of these is checkable against an actual row (``GET
    .../documents/{document_id}/chunks``), not a model-invented reference. This is the entity that
    closes the named RAG gap: nodes previously had no evidence-shaped value to put in a prompt at
    all.
    """

    chunk_id: str
    document_id: str
    text: str
    rank: int


@dataclass(frozen=True)
class Message:
    """One turn in a user's private AI Copilot conversation for one engagement (``agent.messages``).

    Private per ``(engagement_id, user_id)`` — a working session with the assistant, not a
    team-shared record the way a finding or a report is. ``run_id`` is set only when
    ``message_type is MessageType.RUN_REFERENCE``: this turn handed off to the real investigation
    graph (``OrchestrationService.start_run``), and the referenced run carries its own
    plan/evidence/HITL state exactly as it does when started from the task-form UI — nothing about
    that pipeline is duplicated here, this is only a pointer at it.
    """

    id: str
    engagement_id: str
    user_id: str
    role: MessageRole
    message_type: MessageType
    content: str
    run_id: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None


@dataclass
class ToolPermission:
    """One entry in the tool registry: a tool and the agents allowed to call it.

    The registry enforces least-privilege by construction — a call from any agent not in
    ``allowed_agents`` is rejected before it reaches the backing API. Modelled here as data so the
    registry is a reviewable table (``application/tool_registry.py``), not scattered per-call-site
    checks.
    """

    tool_name: str
    allowed_agents: frozenset[str] = field(default_factory=frozenset)
    write: bool = False
