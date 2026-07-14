"""Unit tests for the least-privilege tool registry (Phase 5 §12)."""

from __future__ import annotations

from agent_orchestrator.application.tool_registry import is_tool_allowed, tools_for_agent
from agent_orchestrator.domain.agents import AgentRole


def test_retrieval_agent_may_call_hybrid_search() -> None:
    assert is_tool_allowed(tool_name="hybrid_search", agent=AgentRole.RETRIEVAL) is True


def test_retrieval_agent_may_not_call_run_sql_template() -> None:
    assert is_tool_allowed(tool_name="run_sql_template", agent=AgentRole.RETRIEVAL) is False


def test_unknown_tool_name_denied_by_default() -> None:
    """Fail closed: an unregistered/misspelled tool name is denied, not treated as unrestricted."""
    assert is_tool_allowed(tool_name="delete_everything", agent=AgentRole.PLANNER) is False


def test_submit_finding_draft_allowed_for_every_evidence_specialist() -> None:
    for role in (
        AgentRole.RETRIEVAL,
        AgentRole.SQL,
        AgentRole.KNOWLEDGE_GRAPH,
        AgentRole.FRAUD_DETECTION,
    ):
        assert is_tool_allowed(tool_name="submit_finding_draft", agent=role) is True


def test_submit_finding_draft_not_allowed_for_report_generation() -> None:
    allowed = is_tool_allowed(tool_name="submit_finding_draft", agent=AgentRole.REPORT_GENERATION)
    assert allowed is False


def test_tools_for_agent_returns_exactly_the_registered_tools() -> None:
    tools = tools_for_agent(AgentRole.FRAUD_DETECTION)
    assert tools == frozenset({"get_anomaly_cluster", "get_risk_score", "submit_finding_draft"})


def test_tools_for_agent_empty_for_a_role_with_no_registered_tools() -> None:
    assert tools_for_agent(AgentRole.CONTEXT_ENGINEERING) == frozenset()


def test_copilot_actions_role_may_call_every_direct_action_tool() -> None:
    for tool_name in (
        "import_transactions",
        "run_risk_scan",
        "compute_risk_scores",
        "resolve_vendors",
        "generate_report",
        "generate_embeddings",
    ):
        assert is_tool_allowed(tool_name=tool_name, agent=AgentRole.COPILOT_ACTIONS) is True


def test_direct_action_tools_not_allowed_for_an_evidence_specialist() -> None:
    """The new AI Copilot action tools are scoped to their own role, not folded into an existing
    evidence specialist's grant — a specialist mid-investigation should never be able to trigger a
    data-mutating action like importing transactions."""
    assert is_tool_allowed(tool_name="run_risk_scan", agent=AgentRole.FRAUD_DETECTION) is False
