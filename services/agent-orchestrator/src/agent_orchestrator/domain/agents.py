"""The nine agent identities and the fixed set of run outcomes.

Framework-free: this module names the agents and outcomes the graph is built from, but knows
nothing about LangGraph, HTTP, or the database. The ``application`` layer maps each
:class:`AgentRole` to a concrete graph node; the ``interface`` layer maps each
:class:`RunStatus` to a response. Keeping these as enums here (not bare strings scattered across
the graph builder) is the same discipline ``apps/api`` uses for ``EngagementRole`` /
``AnomalyStatus`` — one authoritative definition, referenced everywhere.
"""

from __future__ import annotations

from enum import Enum


class AgentRole(str, Enum):
    """The nine specialized agents of the master graph.

    The Planner is the orchestration root; the four evidence specialists (Retrieval, SQL,
    Knowledge Graph, Fraud Detection) fan out in parallel; Context Engineering, Evaluation, and
    Guardrail are cross-cutting stages every synthesis passes through; Report Generation runs only
    after human approval.
    """

    PLANNER = "planner"
    RETRIEVAL = "retrieval"
    SQL = "sql"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    FRAUD_DETECTION = "fraud_detection"
    REPORT_GENERATION = "report_generation"
    CONTEXT_ENGINEERING = "context_engineering"
    EVALUATION = "evaluation"
    GUARDRAIL = "guardrail"
    #: The direct-action dispatcher AI Copilot's chat router
    #: (``application/copilot_chat_service.py``) invokes for a request that names one specific
    #: data operation (import transactions, run the anomaly scan, generate a report, ...) rather
    #: than needing the full plan/evidence/evaluate/guardrail investigation pipeline. Kept as its
    #: own role, not folded into an existing one, so
    #: the tool registry's least-privilege table stays an honest record of exactly which tools this
    #: dispatcher — and nothing else — may call.
    COPILOT_ACTIONS = "copilot_actions"


#: The four evidence-gathering specialists that can run concurrently in the fan-out superstep.
#: Named once here so the graph builder and the plan validator agree on exactly which agents are
#: parallelizable, rather than each re-listing them.
EVIDENCE_SPECIALISTS: frozenset[AgentRole] = frozenset(
    {
        AgentRole.RETRIEVAL,
        AgentRole.SQL,
        AgentRole.KNOWLEDGE_GRAPH,
        AgentRole.FRAUD_DETECTION,
    }
)


class RunStatus(str, Enum):
    """Terminal and in-flight states of a single graph execution.

    ``RUNNING`` and ``AWAITING_HUMAN_REVIEW`` are the two non-terminal states; the rest are
    distinct terminal outcomes kept separate rather than collapsed into one generic "failed". A
    run that produced a report ends ``COMPLETED``; one that could not reach a confident answer
    ends ``CANNOT_CONCLUDE``; a guardrail hard stop ends ``HALTED``; an unhandled operational fault
    ends ``FAILED`` (admin-resumable from its last checkpoint).
    """

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    COMPLETED = "completed"
    CANNOT_CONCLUDE = "cannot_conclude"
    HALTED = "halted"
    FAILED = "failed"


class HitlDecision(str, Enum):
    """A reviewer's disposition at a human-in-the-loop interrupt.

    Mirrors the confirm/reject vocabulary ``apps/api``'s reporting context already uses for finding
    disposition — an agent run's HITL gate and a manual finding's sign-off gate are the same
    governance act, so they share the same three outcomes.
    """

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


class MessageRole(str, Enum):
    """Who authored one AI Copilot chat message (``agent.messages`` — the conversational layer
    added alongside the existing task-form/run pipeline, not a replacement for it)."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageType(str, Enum):
    """How a chat message's ``content`` should be read — plain prose, a direct-action tool's real
    result, or a pointer at a full investigation run (its own plan/evidence/HITL state, unchanged)
    that this message handed off to."""

    TEXT = "text"
    ACTION_RESULT = "action_result"
    RUN_REFERENCE = "run_reference"
