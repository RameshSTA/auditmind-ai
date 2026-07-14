"""Tests for build_copilot_actions — each action's handler really delegates to the (faked) API
client, and its result_formatter reads only the real response shape, never fabricating a count."""

from __future__ import annotations

from agent_orchestrator.application.copilot_actions import build_copilot_actions


class FakeCopilotActionsApiClient:
    def __init__(self) -> None:
        self.import_calls: list[list[dict[str, object]]] = []

    async def import_transactions(
        self, *, engagement_id: str, transactions: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        self.import_calls.append(transactions)
        return [{"id": f"t{i}"} for i in range(len(transactions))]

    async def run_risk_scan(self, *, engagement_id: str) -> list[dict[str, object]]:
        return [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]

    async def compute_risk_scores(self, *, engagement_id: str) -> list[dict[str, object]]:
        return [{"id": "s1"}]

    async def resolve_vendors(self, *, engagement_id: str) -> dict[str, object]:
        return {"newly_resolved_count": 4}

    async def generate_report(self, *, engagement_id: str) -> dict[str, object]:
        return {"version": 3, "finding_ids": ["f1", "f2"]}

    async def generate_embeddings(
        self, *, engagement_id: str, document_id: str
    ) -> dict[str, object]:
        return {"document_id": document_id, "embedded_count": 12}


def _action(name: str):  # type: ignore[no-untyped-def]
    actions = build_copilot_actions(FakeCopilotActionsApiClient())
    return next(a for a in actions if a.name == name)


async def test_import_transactions_action_delegates_and_formats_real_count() -> None:
    action = _action("import_transactions")
    result = await action.handler(
        engagement_id="e1", transactions=[{"amount": "1"}, {"amount": "2"}]
    )
    assert action.result_formatter(result) == "Imported 2 transaction(s)."


async def test_run_risk_scan_action_formats_plural_correctly() -> None:
    action = _action("run_risk_scan")
    result = await action.handler(engagement_id="e1")
    assert action.result_formatter(result) == "Scan complete — 3 new anomalies flagged."


async def test_run_risk_scan_action_handles_zero_new_anomalies() -> None:
    class EmptyClient(FakeCopilotActionsApiClient):
        async def run_risk_scan(self, *, engagement_id: str) -> list[dict[str, object]]:
            return []

    actions = build_copilot_actions(EmptyClient())
    action = next(a for a in actions if a.name == "run_risk_scan")
    result = await action.handler(engagement_id="e1")
    assert action.result_formatter(result) == "Scan complete — no new anomalies flagged."


async def test_compute_risk_scores_action() -> None:
    action = _action("compute_risk_scores")
    result = await action.handler(engagement_id="e1")
    assert action.result_formatter(result) == "Computed risk scores for 1 transaction(s)."


async def test_resolve_vendors_action_reads_the_real_count() -> None:
    action = _action("resolve_vendors")
    result = await action.handler(engagement_id="e1")
    assert action.result_formatter(result) == "Resolved 4 new vendor(s)."


async def test_generate_report_action_reads_real_version_and_finding_count() -> None:
    action = _action("generate_report")
    result = await action.handler(engagement_id="e1")
    assert action.result_formatter(result) == "Generated report v3 with 2 confirmed finding(s)."


async def test_generate_embeddings_action_reads_real_embedded_count() -> None:
    action = _action("generate_embeddings")
    result = await action.handler(engagement_id="e1", document_id="doc-1")
    assert action.result_formatter(result) == "Embedded 12 chunk(s)."
