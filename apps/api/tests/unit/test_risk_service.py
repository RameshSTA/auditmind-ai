"""Unit tests for RiskService (Phase 3 §1 application layer) — against in-memory fakes, no
database involved."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from auditmind_api.risk.application.exceptions import InvalidAnomalyTransitionError
from auditmind_api.risk.application.services import RiskService
from auditmind_api.risk.domain.entities import (
    Anomaly,
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    RiskScore,
    Transaction,
)
from auditmind_api.shared.errors import NotFoundError


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.transactions: dict[str, Transaction] = {}

    async def bulk_create(self, transactions: list[Transaction]) -> list[Transaction]:
        for t in transactions:
            self.transactions[t.id] = t
        return transactions

    async def list_for_engagement(self, engagement_id: str) -> list[Transaction]:
        return [t for t in self.transactions.values() if t.engagement_id == engagement_id]


class FakeAnomalyRepository:
    def __init__(self) -> None:
        self.anomalies: dict[str, Anomaly] = {}

    async def bulk_create(self, anomalies: list[Anomaly]) -> list[Anomaly]:
        for a in anomalies:
            self.anomalies[a.id] = a
        return anomalies

    async def list_for_engagement(self, engagement_id: str) -> list[Anomaly]:
        return [a for a in self.anomalies.values() if a.engagement_id == engagement_id]

    async def get(self, anomaly_id: str) -> Anomaly | None:
        return self.anomalies.get(anomaly_id)

    async def list_open_by_type_for_engagement(
        self, *, engagement_id: str, anomaly_type: AnomalyType
    ) -> list[Anomaly]:
        return [
            a
            for a in self.anomalies.values()
            if a.engagement_id == engagement_id
            and a.anomaly_type == anomaly_type
            and a.status == AnomalyStatus.OPEN
        ]

    async def update_disposition(
        self, *, anomaly_id: str, status: AnomalyStatus, reviewed_by: str, reviewed_at: datetime
    ) -> None:
        existing = self.anomalies[anomaly_id]
        self.anomalies[anomaly_id] = Anomaly(
            id=existing.id,
            engagement_id=existing.engagement_id,
            anomaly_type=existing.anomaly_type,
            severity=existing.severity,
            status=status,
            detected_at=existing.detected_at,
            transaction_id=existing.transaction_id,
            details=existing.details,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )


class FakeAuditTrailRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(
        self,
        *,
        engagement_id: str,
        actor_id: str,
        action: str,
        subject_type: str,
        subject_id: str,
        before_state: dict[str, object] | None,
        after_state: dict[str, object] | None,
    ) -> None:
        self.events.append(
            {
                "engagement_id": engagement_id,
                "actor_id": actor_id,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "before_state": before_state,
                "after_state": after_state,
            }
        )


class FakeRiskScoreRepository:
    def __init__(self) -> None:
        # keyed by (subject_type, subject_id, score_version), matching the real upsert's
        # uniqueness constraint
        self.scores: dict[tuple[str, str, str], RiskScore] = {}

    async def bulk_upsert(self, scores: list[RiskScore]) -> list[RiskScore]:
        for s in scores:
            self.scores[(s.subject_type, s.subject_id, s.score_version)] = s
        return scores

    async def list_for_engagement(self, engagement_id: str) -> list[RiskScore]:
        return [s for s in self.scores.values() if s.engagement_id == engagement_id]


class FakeVendorCentralitySource:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.counts = counts or {}

    async def get_vendor_transaction_counts(self, *, engagement_id: str) -> dict[str, int]:
        return self.counts


@pytest.fixture
def transaction_repo() -> FakeTransactionRepository:
    return FakeTransactionRepository()


@pytest.fixture
def anomaly_repo() -> FakeAnomalyRepository:
    return FakeAnomalyRepository()


def make_service(
    transaction_repo: FakeTransactionRepository,
    anomaly_repo: FakeAnomalyRepository,
    audit_recorder: FakeAuditTrailRecorder | None = None,
    risk_score_repo: FakeRiskScoreRepository | None = None,
    vendor_centrality_source: FakeVendorCentralitySource | None = None,
) -> RiskService:
    return RiskService(
        transaction_repository=transaction_repo,
        anomaly_repository=anomaly_repo,
        audit_recorder=audit_recorder or FakeAuditTrailRecorder(),
        risk_score_repository=risk_score_repo or FakeRiskScoreRepository(),
        vendor_centrality_source=vendor_centrality_source or FakeVendorCentralitySource(),
    )


def transaction_record(
    *, amount: str, transaction_date: date, vendor_name: str = "Acme"
) -> dict[str, Any]:
    return {
        "amount": Decimal(amount),
        "currency": "USD",
        "source_system": "manual_import",
        "transaction_date": transaction_date,
        "raw_payload": {"amount": amount, "vendor_name": vendor_name},
    }


async def test_import_transactions_persists_records(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    service = make_service(transaction_repo, anomaly_repo)

    result = await service.import_transactions(
        engagement_id="eng-1",
        records=[transaction_record(amount="500.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )

    assert len(result) == 1
    assert result[0].engagement_id == "eng-1"
    assert result[0].amount == Decimal("500.00")


async def test_scan_detects_a_round_dollar_transaction(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    service = make_service(transaction_repo, anomaly_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[transaction_record(amount="5000.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )

    anomalies = await service.scan_for_anomalies(engagement_id="eng-1")

    assert any(a.anomaly_type == AnomalyType.ROUND_DOLLAR for a in anomalies)


async def test_scan_is_idempotent_for_already_open_anomalies(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    service = make_service(transaction_repo, anomaly_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[transaction_record(amount="5000.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )

    first_scan = await service.scan_for_anomalies(engagement_id="eng-1")
    second_scan = await service.scan_for_anomalies(engagement_id="eng-1")

    assert len(first_scan) >= 1
    assert second_scan == []  # nothing new — the same open anomalies still exist


async def test_scan_rescans_after_the_prior_anomaly_is_dispositioned(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    """Dedup is scoped to *open* anomalies only — once reviewed, a fresh scan over the same
    transaction should be free to flag it again if it still matches (e.g. a false positive marked
    reviewed shouldn't permanently suppress future genuine detections on the same transaction)."""
    service = make_service(transaction_repo, anomaly_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[transaction_record(amount="5000.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )
    first_scan = await service.scan_for_anomalies(engagement_id="eng-1")
    round_dollar = next(a for a in first_scan if a.anomaly_type == AnomalyType.ROUND_DOLLAR)
    await service.disposition_anomaly(
        anomaly_id=round_dollar.id, status=AnomalyStatus.FALSE_POSITIVE, reviewed_by="reviewer-1"
    )

    second_scan = await service.scan_for_anomalies(engagement_id="eng-1")

    assert any(a.anomaly_type == AnomalyType.ROUND_DOLLAR for a in second_scan)


async def test_disposition_anomaly_transitions_open_to_true_positive(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    anomaly = Anomaly(
        id=str(uuid.uuid4()),
        engagement_id="eng-1",
        anomaly_type=AnomalyType.ROUND_DOLLAR,
        severity=AnomalySeverity.LOW,
        status=AnomalyStatus.OPEN,
        detected_at=datetime.now(),
    )
    anomaly_repo.anomalies[anomaly.id] = anomaly
    service = make_service(transaction_repo, anomaly_repo)

    result = await service.disposition_anomaly(
        anomaly_id=anomaly.id, status=AnomalyStatus.TRUE_POSITIVE, reviewed_by="reviewer-1"
    )

    assert result.status == AnomalyStatus.TRUE_POSITIVE
    assert result.reviewed_by == "reviewer-1"


async def test_disposition_anomaly_records_an_audit_trail_event(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    anomaly = Anomaly(
        id=str(uuid.uuid4()),
        engagement_id="eng-1",
        anomaly_type=AnomalyType.ROUND_DOLLAR,
        severity=AnomalySeverity.LOW,
        status=AnomalyStatus.OPEN,
        detected_at=datetime.now(),
    )
    anomaly_repo.anomalies[anomaly.id] = anomaly
    audit_recorder = FakeAuditTrailRecorder()
    service = make_service(transaction_repo, anomaly_repo, audit_recorder=audit_recorder)

    await service.disposition_anomaly(
        anomaly_id=anomaly.id, status=AnomalyStatus.TRUE_POSITIVE, reviewed_by="reviewer-1"
    )

    assert len(audit_recorder.events) == 1
    event = audit_recorder.events[0]
    assert event["action"] == "anomaly.true_positive"
    assert event["subject_id"] == anomaly.id
    assert event["actor_id"] == "reviewer-1"


async def test_disposition_anomaly_raises_when_already_dispositioned(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    anomaly = Anomaly(
        id=str(uuid.uuid4()),
        engagement_id="eng-1",
        anomaly_type=AnomalyType.ROUND_DOLLAR,
        severity=AnomalySeverity.LOW,
        status=AnomalyStatus.TRUE_POSITIVE,
        detected_at=datetime.now(),
    )
    anomaly_repo.anomalies[anomaly.id] = anomaly
    service = make_service(transaction_repo, anomaly_repo)

    with pytest.raises(InvalidAnomalyTransitionError):
        await service.disposition_anomaly(
            anomaly_id=anomaly.id, status=AnomalyStatus.FALSE_POSITIVE, reviewed_by="reviewer-1"
        )


async def test_get_anomaly_raises_not_found_for_unknown_id(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    service = make_service(transaction_repo, anomaly_repo)

    with pytest.raises(NotFoundError):
        await service.get_anomaly(str(uuid.uuid4()))


async def test_compute_risk_scores_returns_empty_for_an_engagement_with_no_transactions(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    service = make_service(transaction_repo, anomaly_repo)

    scores = await service.compute_risk_scores(engagement_id="eng-1")

    assert scores == []


async def test_compute_risk_scores_persists_one_score_per_transaction(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    service = make_service(transaction_repo, anomaly_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[
            transaction_record(amount="500.00", transaction_date=date(2026, 1, 1)),
            transaction_record(amount="700.00", transaction_date=date(2026, 1, 2)),
        ],
        created_by="user-1",
    )

    scores = await service.compute_risk_scores(engagement_id="eng-1")

    assert len(scores) == 2
    assert all(s.subject_type == "transaction" for s in scores)
    assert all(s.engagement_id == "eng-1" for s in scores)


async def test_compute_risk_scores_incorporates_the_rule_engines_open_anomaly(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    """A transaction with an open round-dollar anomaly should carry a `rule_engine` contributing
    factor; one with no open anomaly of its own should not."""
    service = make_service(transaction_repo, anomaly_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[
            transaction_record(amount="5000.00", transaction_date=date(2026, 1, 1)),  # round dollar
            transaction_record(amount="123.45", transaction_date=date(2026, 1, 2)),
        ],
        created_by="user-1",
    )
    await service.scan_for_anomalies(engagement_id="eng-1")

    scores = await service.compute_risk_scores(engagement_id="eng-1")

    round_dollar_score = next(s for s in scores if s.score == max(s.score for s in scores))
    assert "rule_engine" in round_dollar_score.contributing_factors


async def test_compute_risk_scores_incorporates_graph_centrality_for_a_known_vendor(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    vendor_centrality_source = FakeVendorCentralitySource(counts={"acme": 1})
    service = make_service(
        transaction_repo, anomaly_repo, vendor_centrality_source=vendor_centrality_source
    )
    await service.import_transactions(
        engagement_id="eng-1",
        records=[
            transaction_record(amount="500.00", transaction_date=date(2026, 1, 1))
        ],
        created_by="user-1",
    )

    scores = await service.compute_risk_scores(engagement_id="eng-1")

    assert "graph_centrality" in scores[0].contributing_factors
    # A vendor with only 1 transaction is far from "established" (the threshold is 10) — expect a
    # high, not low, contribution.
    assert scores[0].contributing_factors["graph_centrality"]["value"] > 50.0


async def test_compute_risk_scores_omits_graph_centrality_when_vendor_unresolved(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    """No knowledge-graph resolution has run for this engagement (the fake centrality source
    returns nothing) — the signal is simply absent, not defaulted to some assumed value."""
    service = make_service(transaction_repo, anomaly_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[
            transaction_record(amount="500.00", transaction_date=date(2026, 1, 1))
        ],
        created_by="user-1",
    )

    scores = await service.compute_risk_scores(engagement_id="eng-1")

    assert "graph_centrality" not in scores[0].contributing_factors


async def test_compute_risk_scores_is_idempotent_via_upsert(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    risk_score_repo = FakeRiskScoreRepository()
    service = make_service(transaction_repo, anomaly_repo, risk_score_repo=risk_score_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[transaction_record(amount="500.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )

    await service.compute_risk_scores(engagement_id="eng-1")
    await service.compute_risk_scores(engagement_id="eng-1")

    assert len(risk_score_repo.scores) == 1  # refreshed in place, not accumulated


async def test_list_risk_scores_scoped_to_the_requested_engagement_only(
    transaction_repo: FakeTransactionRepository, anomaly_repo: FakeAnomalyRepository
) -> None:
    risk_score_repo = FakeRiskScoreRepository()
    service = make_service(transaction_repo, anomaly_repo, risk_score_repo=risk_score_repo)
    await service.import_transactions(
        engagement_id="eng-1",
        records=[transaction_record(amount="500.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )
    await service.import_transactions(
        engagement_id="eng-2",
        records=[transaction_record(amount="600.00", transaction_date=date(2026, 1, 1))],
        created_by="user-1",
    )
    await service.compute_risk_scores(engagement_id="eng-1")
    await service.compute_risk_scores(engagement_id="eng-2")

    scores = await service.list_risk_scores("eng-1")

    assert len(scores) == 1
    assert scores[0].engagement_id == "eng-1"
