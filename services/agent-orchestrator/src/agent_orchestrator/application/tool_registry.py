"""The tool registry — least-privilege tool access, enforced by construction.

A reviewed table lists exactly which agent may call which tool. Encoding that as a reviewed table
here (rather than as scattered per-call-site checks) means the least-privilege rule is one
auditable artifact: a call from any agent not listed for a tool is rejected before it reaches the
backing API. This is the graph-runtime half of the same "remove the capability rather than prompt
against it" discipline this codebase applies to raw SQL.

Only the *authorization* half of the registry is built here. The tools themselves (``hybrid_search``
against ``apps/api``'s ``/v1/search``, ``graph_traverse`` against ``/v1/graph``, etc.) are HTTP
calls into the core API that this service does not yet make — the *implementations* are deferred
while the *registry that governs them* is built now.
"""

from __future__ import annotations

from agent_orchestrator.domain.agents import AgentRole
from agent_orchestrator.domain.entities import ToolPermission

# Each tool names the agent(s) permitted to call it; the write-capable tools
# (``submit_finding_draft``, ``request_human_review``) are flagged so a future rate-limit /
# circuit-breaker policy can treat writes distinctly.
_REGISTRY: dict[str, ToolPermission] = {
    "hybrid_search": ToolPermission(
        tool_name="hybrid_search", allowed_agents=frozenset({AgentRole.RETRIEVAL.value})
    ),
    "get_document": ToolPermission(
        tool_name="get_document", allowed_agents=frozenset({AgentRole.RETRIEVAL.value})
    ),
    "run_sql_template": ToolPermission(
        tool_name="run_sql_template", allowed_agents=frozenset({AgentRole.SQL.value})
    ),
    "graph_traverse": ToolPermission(
        tool_name="graph_traverse", allowed_agents=frozenset({AgentRole.KNOWLEDGE_GRAPH.value})
    ),
    "get_anomaly_cluster": ToolPermission(
        tool_name="get_anomaly_cluster",
        allowed_agents=frozenset({AgentRole.FRAUD_DETECTION.value}),
    ),
    "get_risk_score": ToolPermission(
        tool_name="get_risk_score", allowed_agents=frozenset({AgentRole.FRAUD_DETECTION.value})
    ),
    "get_confirmed_findings": ToolPermission(
        tool_name="get_confirmed_findings",
        allowed_agents=frozenset({AgentRole.REPORT_GENERATION.value}),
    ),
    # submit_finding_draft is the one tool any specialist may call (post-Guardrail) — the write
    # path into apps/api's reporting context. Idempotent by design (keyed on an idempotency key).
    "submit_finding_draft": ToolPermission(
        tool_name="submit_finding_draft",
        allowed_agents=frozenset(
            {
                AgentRole.RETRIEVAL.value,
                AgentRole.SQL.value,
                AgentRole.KNOWLEDGE_GRAPH.value,
                AgentRole.FRAUD_DETECTION.value,
            }
        ),
        write=True,
    ),
    # AI Copilot's direct-action dispatcher (application/copilot_chat_service.py) — a chat message
    # that names one specific data operation rather than needing the full investigation pipeline.
    # All six wrap a confirmed, already-existing apps/api endpoint (infrastructure/api_client.py);
    # none of them invent a new capability on the apps/api side.
    "import_transactions": ToolPermission(
        tool_name="import_transactions",
        allowed_agents=frozenset({AgentRole.COPILOT_ACTIONS.value}),
        write=True,
    ),
    "run_risk_scan": ToolPermission(
        tool_name="run_risk_scan",
        allowed_agents=frozenset({AgentRole.COPILOT_ACTIONS.value}),
        write=True,
    ),
    "compute_risk_scores": ToolPermission(
        tool_name="compute_risk_scores",
        allowed_agents=frozenset({AgentRole.COPILOT_ACTIONS.value}),
        write=True,
    ),
    "resolve_vendors": ToolPermission(
        tool_name="resolve_vendors",
        allowed_agents=frozenset({AgentRole.COPILOT_ACTIONS.value}),
        write=True,
    ),
    "generate_report": ToolPermission(
        tool_name="generate_report",
        allowed_agents=frozenset({AgentRole.COPILOT_ACTIONS.value}),
        write=True,
    ),
    "generate_embeddings": ToolPermission(
        tool_name="generate_embeddings",
        allowed_agents=frozenset({AgentRole.COPILOT_ACTIONS.value}),
        write=True,
    ),
}


def is_tool_allowed(*, tool_name: str, agent: AgentRole) -> bool:
    """Whether ``agent`` is permitted to call ``tool_name`` per the registry.

    An unknown tool name is denied (returns ``False``), not treated as unrestricted — the safe
    default for a least-privilege registry is deny, so a typo or an unregistered tool fails closed.
    """
    permission = _REGISTRY.get(tool_name)
    if permission is None:
        return False
    return agent.value in permission.allowed_agents


def tools_for_agent(agent: AgentRole) -> frozenset[str]:
    """Every tool ``agent`` is permitted to call — backs the Planner's ``list_available_tools``,
    which queries the registry for what a task type may use before writing the plan.
    """
    return frozenset(name for name, perm in _REGISTRY.items() if agent.value in perm.allowed_agents)
