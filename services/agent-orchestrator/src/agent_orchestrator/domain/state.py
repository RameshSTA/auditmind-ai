"""The LangGraph state schema and its channel reducers.

This is the single most load-bearing module in the service: the thirteen state channels here *are*
the audit trail of a run — the plan itself, persisted in state and checkpointed, is the audit trail
of why the system did what it did. Every channel is a plain, JSON-serializable value — no custom
object graphs — because the Postgres checkpointer persists the whole state verbatim at each step
boundary, and a non-serializable channel would be a silent gap in that trail.

The reducers below are the merge behavior each channel needs:

    * append (``append_reducer``) — the three evidence channels three parallel agents write to
      concurrently. Append-only means three concurrent writers to three distinct channels can never
      race on the same value. This is what makes the parallel fan-out safe.
    * overwrite (``overwrite_reducer``) — plan, fraud_signals, engineered_context, evaluation_score:
      each is regenerated wholesale by its owning agent; the prior value survives only in the
      checkpoint history, not the live channel.
    * increment (``increment_reducer``) — retry_count, the Planner's re-plan counter.

Kept framework-free deliberately: these are ordinary typed functions and a ``TypedDict``. The
``application`` layer wraps each channel in ``typing.Annotated[..., reducer]`` when it hands the
schema to ``langgraph.StateGraph`` — so the reducer *semantics* live in the domain (testable in
isolation, no LangGraph import), and only the *binding* to LangGraph lives one layer up.
"""

from __future__ import annotations

from typing import Any, TypedDict


def append_reducer(existing: list[Any] | None, incoming: list[Any]) -> list[Any]:
    """Append-only merge for the parallel-safe evidence channels.

    ``existing`` is ``None`` on the first write to a channel (LangGraph initializes an unset
    annotated-list channel lazily), so this normalizes that to an empty list rather than raising —
    the same shape LangGraph's own ``operator.add``-style reducers tolerate.
    """
    return [*(existing or []), *incoming]


def overwrite_reducer(_existing: Any, incoming: Any) -> Any:
    """Last-writer-wins merge for regenerated channels (plan, fraud_signals, ...).

    The prior value is intentionally discarded from the live channel — it is preserved in the
    checkpoint history, which is where a re-plan's earlier plan or an earlier evaluation score is
    recovered for governance replay, not from the current state.
    """
    return incoming


def increment_reducer(existing: int | None, incoming: int) -> int:
    """Additive merge for ``retry_count``.

    A node signals 'count one more re-plan' by writing ``1`` to the channel; this adds it to the
    running total. Writing the absolute value instead would make a node need to first read the
    current count, which annotated channels do not cleanly support under parallel writes.
    """
    return (existing or 0) + incoming


class PlanStep(TypedDict):
    """One sub-task in the Planner's decomposition.

    ``agent`` is an :class:`~agent_orchestrator.domain.agents.AgentRole` value (stored as its string
    form so the whole step is JSON-serializable for the checkpointer). ``depends_on`` lists the
    ``step_id`` values that must finish before this step may start — the Planner marks two steps as
    parallelizable precisely by giving neither a ``depends_on`` edge to the other.
    """

    step_id: str
    agent: str
    objective: str
    depends_on: list[str]


class AgentState(TypedDict, total=False):
    """The thirteen state channels of a run (twelve core channels plus ``submitted_finding_id``
    added for the RAG write-back), plus the run's own identity.

    ``total=False`` because a run starts with only ``task`` / ``run_id`` / ``engagement_id`` set;
    every other channel is populated by the node that owns it as the graph advances. Each channel
    is annotated in the graph builder (``application/graph.py``) with its matching reducer.
    """

    # --- run identity (set once at entry, carried through every checkpoint) ---
    run_id: str
    engagement_id: str
    initiated_by: str

    # --- the twelve core channels ---
    task: str  # set once (API entry)
    plan: list[PlanStep]  # overwrite (Planner; prior plan preserved in checkpoint history)
    evidence_retrieval: list[dict[str, Any]]  # append (Retrieval Agent — parallel-safe)
    sql_results: list[dict[str, Any]]  # append (SQL Agent)
    graph_context: list[dict[str, Any]]  # append (Knowledge Graph Agent)
    fraud_signals: dict[str, Any]  # overwrite (Fraud Detection Agent)
    engineered_context: str  # overwrite (Context Engineering — regenerated each synthesis pass)
    evaluation_score: float  # overwrite (Evaluation Agent)
    guardrail_flags: list[dict[str, Any]]  # append (Guardrail Agent)
    hitl_decision: str  # set once per interrupt (HITL node)
    retry_count: int  # increment (Planner)
    final_output: dict[str, Any]  # set once (Report Generation Agent)
    submitted_finding_id: str  # overwrite (Guardrail-out — the real apps/api finding this run's
    # evidence was written to via submit_finding_draft, if the scan came back clean; empty if not)
