"""Unit tests for the graph's pure routing decisions."""

from __future__ import annotations

from agent_orchestrator.application.routing import (
    EVALUATION_CONFIDENCE_FLOOR,
    route_after_evaluation,
    route_after_guardrail_in,
    route_after_guardrail_out,
    route_after_hitl,
    specialists_for_plan,
)
from agent_orchestrator.domain.agents import AgentRole
from agent_orchestrator.domain.state import PlanStep


def _step(agent: str, step_id: str = "s1") -> PlanStep:
    return {"step_id": step_id, "agent": agent, "objective": "x", "depends_on": []}


def test_specialists_for_plan_returns_distinct_specialists_in_declaration_order() -> None:
    plan = [
        _step(AgentRole.FRAUD_DETECTION.value, "s1"),
        _step(AgentRole.RETRIEVAL.value, "s2"),
        _step(AgentRole.RETRIEVAL.value, "s3"),  # duplicate agent, same plan
    ]
    assert specialists_for_plan(plan) == [AgentRole.RETRIEVAL, AgentRole.FRAUD_DETECTION]


def test_specialists_for_plan_excludes_non_specialist_roles() -> None:
    plan = [_step(AgentRole.PLANNER.value), _step(AgentRole.REPORT_GENERATION.value)]
    assert specialists_for_plan(plan) == []


def test_specialists_for_plan_empty_for_empty_plan() -> None:
    assert specialists_for_plan([]) == []


def test_route_after_evaluation_proceeds_at_or_above_floor() -> None:
    state = {"evaluation_score": EVALUATION_CONFIDENCE_FLOOR}
    assert route_after_evaluation(state, max_replans=2) == "proceed"


def test_route_after_evaluation_replans_below_floor_with_budget_remaining() -> None:
    state = {"evaluation_score": 0.1, "retry_count": 0}
    assert route_after_evaluation(state, max_replans=2) == "replan"


def test_route_after_evaluation_cannot_conclude_when_budget_exhausted() -> None:
    state = {"evaluation_score": 0.1, "retry_count": 2}
    assert route_after_evaluation(state, max_replans=2) == "cannot_conclude"


def test_route_after_guardrail_in_halts_on_prompt_injection_violation() -> None:
    state = {
        "guardrail_flags": [
            {"kind": "prompt_injection", "severity": "violation", "pattern": "ignore previous"}
        ]
    }
    assert route_after_guardrail_in(state) == "halt"


def test_route_after_guardrail_in_proceeds_with_no_flags() -> None:
    assert route_after_guardrail_in({"guardrail_flags": []}) == "proceed"


def test_route_after_guardrail_in_ignores_non_injection_flags() -> None:
    """A `violation`-severity flag of a *different* kind (e.g. a PII flag written before this
    function runs) must not trip the input-scan router — it checks specifically for
    `kind == "prompt_injection"`."""
    state = {"guardrail_flags": [{"kind": "pii", "severity": "violation", "pattern": "x"}]}
    assert route_after_guardrail_in(state) == "proceed"


def test_route_after_guardrail_out_halts_on_any_violation() -> None:
    state = {"guardrail_flags": [{"kind": "pii", "severity": "violation", "pattern": "ssn_like"}]}
    assert route_after_guardrail_out(state) == "halt"


def test_route_after_guardrail_out_awaits_review_on_soft_flag() -> None:
    state = {"guardrail_flags": [{"kind": "pii", "severity": "soft", "pattern": "ssn_like"}]}
    assert route_after_guardrail_out(state) == "await_review"


def test_route_after_guardrail_out_awaits_review_with_no_flags() -> None:
    assert route_after_guardrail_out({"guardrail_flags": []}) == "await_review"


def test_route_after_hitl_reports_on_approve() -> None:
    assert route_after_hitl({"hitl_decision": "approve"}) == "report"


def test_route_after_hitl_ends_on_reject() -> None:
    assert route_after_hitl({"hitl_decision": "reject"}) == "end"


def test_route_after_hitl_ends_on_edit() -> None:
    assert route_after_hitl({"hitl_decision": "edit"}) == "end"
