"""Builds the concrete list of :class:`CopilotAction`\\ s AI Copilot's chat router can dispatch
to — one entry per direct-action tool registered under ``AgentRole.COPILOT_ACTIONS``
(``application/tool_registry.py``), each a thin adapter over one confirmed
:class:`HttpApiClient` method (``infrastructure/api_client.py``).

Kept as its own module (not inlined into ``interface/dependencies.py``) so the *content* of what
each action says back to the user lives next to the actions themselves, not buried in FastAPI
wiring — and so it stays independently unit-testable without constructing the whole DI graph.
"""

from __future__ import annotations

from agent_orchestrator.application.copilot_chat_service import CopilotAction
from agent_orchestrator.domain.ports import CopilotActionsApiClient


def _format_import_transactions(result: dict[str, object]) -> str:
    transactions = result.get("transactions")
    count = len(transactions) if isinstance(transactions, list) else 0
    return f"Imported {count} transaction(s)."


def _format_risk_scan(result: dict[str, object]) -> str:
    anomalies = result.get("anomalies")
    count = len(anomalies) if isinstance(anomalies, list) else 0
    if count == 0:
        return "Scan complete — no new anomalies flagged."
    word = "anomaly" if count == 1 else "anomalies"
    return f"Scan complete — {count} new {word} flagged."


def _format_risk_scores(result: dict[str, object]) -> str:
    scores = result.get("scores")
    count = len(scores) if isinstance(scores, list) else 0
    return f"Computed risk scores for {count} transaction(s)."


def _format_resolve_vendors(result: dict[str, object]) -> str:
    count = result.get("newly_resolved_count")
    return f"Resolved {count if isinstance(count, int) else 0} new vendor(s)."


def _format_generate_report(result: dict[str, object]) -> str:
    version = result.get("version")
    finding_ids = result.get("finding_ids")
    count = len(finding_ids) if isinstance(finding_ids, list) else 0
    return f"Generated report v{version} with {count} confirmed finding(s)."


def _format_generate_embeddings(result: dict[str, object]) -> str:
    count = result.get("embedded_count")
    return f"Embedded {count if isinstance(count, int) else 0} chunk(s)."


def build_copilot_actions(api_client: CopilotActionsApiClient) -> list[CopilotAction]:
    """One :class:`CopilotAction` per tool ``tool_registry.py`` grants ``AgentRole.COPILOT_ACTIONS``
    — list-returning methods get a tiny adapter that boxes the list into a dict
    (``CopilotAction.handler`` is uniformly ``dict``-in/``dict``-out, so every result formatter has
    the same shape to read from); the three that already return a dict are passed straight through.
    Takes the port, not the concrete ``HttpApiClient``, so this stays testable against a fake.
    """

    async def _import_transactions(
        *, engagement_id: str, transactions: list[dict[str, object]]
    ) -> dict[str, object]:
        created = await api_client.import_transactions(
            engagement_id=engagement_id, transactions=transactions
        )
        return {"transactions": created}

    async def _run_risk_scan(*, engagement_id: str) -> dict[str, object]:
        anomalies = await api_client.run_risk_scan(engagement_id=engagement_id)
        return {"anomalies": anomalies}

    async def _compute_risk_scores(*, engagement_id: str) -> dict[str, object]:
        scores = await api_client.compute_risk_scores(engagement_id=engagement_id)
        return {"scores": scores}

    return [
        CopilotAction(
            name="import_transactions",
            description="import a batch of transactions",
            handler=_import_transactions,
            result_formatter=_format_import_transactions,
        ),
        CopilotAction(
            name="run_risk_scan",
            description="run the rule-engine anomaly scan",
            handler=_run_risk_scan,
            result_formatter=_format_risk_scan,
        ),
        CopilotAction(
            name="compute_risk_scores",
            description="compute risk scores for this engagement's transactions",
            handler=_compute_risk_scores,
            result_formatter=_format_risk_scores,
        ),
        CopilotAction(
            name="resolve_vendors",
            description="resolve vendor relationships from transaction data",
            handler=api_client.resolve_vendors,
            result_formatter=_format_resolve_vendors,
        ),
        CopilotAction(
            name="generate_report",
            description="generate a new versioned report from confirmed findings",
            handler=api_client.generate_report,
            result_formatter=_format_generate_report,
        ),
        CopilotAction(
            name="generate_embeddings",
            description="generate semantic search embeddings for a document",
            handler=api_client.generate_embeddings,
            result_formatter=_format_generate_embeddings,
        ),
    ]
