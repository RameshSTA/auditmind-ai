"""Pure routing logic for the master graph (Phase 5 §1, §13, §14, §17).

Every conditional edge in the graph delegates the actual *decision* to a pure function here — the
graph builder (``graph.py``) only wires these functions in as LangGraph ``add_conditional_edges``
routers. Keeping the decisions pure and framework-free means the routing that determines a run's
control flow — which specialists fan out, whether Evaluation triggers a re-plan, whether the run
can conclude — is unit-testable in isolation from LangGraph, the checkpointer, or any LLM. This
mirrors how ``apps/api`` keeps its rule-engine decisions (Benford, duplicate detection) as pure
functions the service orchestrates, not tangled into I/O.

The threshold constants here are the ones Phase 5 names: the evaluation confidence floor below
which a draft is re-planned rather than passed forward (§8), and the re-plan ceiling from §14.
"""

from __future__ import annotations

from agent_orchestrator.domain.agents import EVIDENCE_SPECIALISTS, AgentRole
from agent_orchestrator.domain.state import AgentState, PlanStep

#: Evaluation confidence below this routes back to the Planner for a bounded re-plan (Phase 5 §8,
#: §14) instead of passing a shaky draft to Guardrail-out. A plausible default for the scaffold;
#: the real value is an evaluation-harness-calibrated number (Phase 9), documented as such here so
#: it is not mistaken for a tuned production threshold.
EVALUATION_CONFIDENCE_FLOOR: float = 0.7


def specialists_for_plan(plan: list[PlanStep]) -> list[AgentRole]:
    """The distinct evidence specialists a plan dispatches to, in a stable order (Phase 5 §13).

    Reads the plan the Planner wrote into state and returns which of the four evidence specialists
    (Retrieval / SQL / Knowledge Graph / Fraud Detection) have at least one sub-task. This is the
    fan-out router: the graph dispatches exactly these branches in parallel. Deduplicated and
    ordered by :class:`AgentRole` declaration order so the fan-out set is deterministic (important
    for a reproducible, checkpoint-replayable run), never dependent on plan-step ordering.
    """
    wanted = {
        AgentRole(step["agent"])
        for step in plan
        if AgentRole(step["agent"]) in EVIDENCE_SPECIALISTS
    }
    return [role for role in AgentRole if role in wanted]


def route_after_evaluation(state: AgentState, *, max_replans: int) -> str:
    """Decide what happens after the Evaluation Agent scores a draft (Phase 5 §8, §14, §17).

    Three outcomes, exactly the distinct failure/success handling Phase 5 §17 insists on:

    * confidence at or above the floor -> ``"proceed"`` (on to Guardrail-out, then HITL).
    * confidence below the floor, re-plan budget remaining -> ``"replan"`` (back to the Planner
      with a broadened scope, §14's bounded re-plan).
    * confidence below the floor, budget exhausted -> ``"cannot_conclude"`` (AC-01's honest
      terminal, §17 — admit uncertainty rather than pass a shaky draft forward).

    Returns a string label (not an enum) because that is what LangGraph's conditional-edge mapping
    keys on; the label set is small and fixed, asserted by the graph builder's edge map.
    """
    score = state.get("evaluation_score", 0.0)
    if score >= EVALUATION_CONFIDENCE_FLOOR:
        return "proceed"
    if state.get("retry_count", 0) < max_replans:
        return "replan"
    return "cannot_conclude"


def route_after_guardrail_in(state: AgentState) -> str:
    """Decide what happens after the input Guardrail scan at entry (Phase 5 §9, §17).

    A ``violation``-severity flag from the input scan (a detected prompt-injection pattern in the
    incoming task) is a hard stop before any planning or evidence gathering — the run halts (§17),
    never proceeds to a reasoning agent whose context the injected instruction could poison (§9's
    reason for scanning on *input*, not only on output). Anything else proceeds to the Planner.

    Note this reads the *same* ``guardrail_flags`` channel the output scan also appends to, so it
    checks specifically for a ``kind == 'prompt_injection'`` violation — an input-scan concern —
    rather than any violation, which keeps the two guardrail points' routing independent even though
    they share one append-only channel (§11).
    """
    flags = state.get("guardrail_flags", [])
    if any(
        flag.get("severity") == "violation" and flag.get("kind") == "prompt_injection"
        for flag in flags
    ):
        return "halt"
    return "proceed"


def route_after_guardrail_out(state: AgentState) -> str:
    """Decide what happens after the output Guardrail scan (Phase 5 §9, §17).

    A guardrail *violation* (a hard flag in ``guardrail_flags``) is a hard stop — never retried,
    the run halts and (in a fuller build) alerts governance (§9, §17). Anything else proceeds to
    the human-in-the-loop interrupt. This is the 'is this safe and compliant to show' gate, kept
    strictly distinct from Evaluation's 'is this true and well-supported' gate (§9).
    """
    flags = state.get("guardrail_flags", [])
    if any(flag.get("severity") == "violation" for flag in flags):
        return "halt"
    return "await_review"


def route_after_hitl(state: AgentState) -> str:
    """Decide what happens after a reviewer dispositions the interrupt (Phase 5 §15).

    Approve -> Report Generation runs (§6). Reject or edit -> the run ends without a published
    report, both the AI draft and the human decision preserved in the audit trail (§15) — this
    router only chooses the *graph* path; recording both versions is the HITL node's own write.
    """
    decision = state.get("hitl_decision")
    if decision == "approve":
        return "report"
    return "end"
