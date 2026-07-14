"""Application service orchestrating transaction import and the rule-engine scan.

No SQL, no HTTP, no framework import here — only coordination of the ports and the pure rule
functions in ``rules.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from auditmind_api.risk.application.combiner import combine_signals
from auditmind_api.risk.application.exceptions import InvalidAnomalyTransitionError
from auditmind_api.risk.application.features import build_feature_matrix, normalized_vendor_name
from auditmind_api.risk.application.ml_signals import (
    compute_hdbscan_noise_flags,
    compute_isolation_forest_scores,
)
from auditmind_api.risk.application.model_validation import (
    ModelValidationResult,
    ablate_combiner,
    cross_validate_isolation_forest,
    hdbscan_stability,
)
from auditmind_api.risk.application.rules import (
    AnomalyCandidate,
    detect_benford_deviation,
    detect_duplicate_payments,
    detect_threshold_and_round_dollar,
)
from auditmind_api.risk.domain.entities import (
    Anomaly,
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    RiskScore,
    Transaction,
)
from auditmind_api.risk.domain.ports import (
    AnomalyRepository,
    AuditTrailRecorder,
    RiskScoreRepository,
    TransactionRepository,
    VendorCentralitySource,
)
from auditmind_api.shared.errors import NotFoundError

_SEVERITY_RANK: dict[AnomalySeverity, int] = {
    AnomalySeverity.LOW: 0,
    AnomalySeverity.MEDIUM: 1,
    AnomalySeverity.HIGH: 2,
    AnomalySeverity.CRITICAL: 3,
}

_CURRENT_SCORE_VERSION = "v1"


class RiskService:
    def __init__(
        self,
        transaction_repository: TransactionRepository,
        anomaly_repository: AnomalyRepository,
        audit_recorder: AuditTrailRecorder,
        risk_score_repository: RiskScoreRepository,
        vendor_centrality_source: VendorCentralitySource,
    ) -> None:
        self._transactions = transaction_repository
        self._anomalies = anomaly_repository
        self._audit = audit_recorder
        self._risk_scores = risk_score_repository
        self._vendor_centrality = vendor_centrality_source

    async def import_transactions(
        self,
        *,
        engagement_id: str,
        records: list[dict[str, Any]],
        created_by: str,
    ) -> list[Transaction]:
        """Bulk-imports transaction records for an engagement.

        Each record must already carry properly-typed ``amount`` (``Decimal``), ``currency``,
        ``source_system``, ``transaction_date`` (``date``), and a JSON-safe ``raw_payload`` —
        parsing and JSON-safety are the interface layer's job; this layer only coordinates. There
        is no ERP connector, so this is the only path transaction data enters the platform.
        """
        now = datetime.now(UTC)
        transactions = [
            Transaction(
                id=str(uuid.uuid4()),
                engagement_id=engagement_id,
                source_system=record["source_system"],
                amount=record["amount"],
                currency=record["currency"],
                transaction_date=record["transaction_date"],
                raw_payload=record["raw_payload"],
                created_by=created_by,
                created_at=now,
            )
            for record in records
        ]
        return await self._transactions.bulk_create(transactions)

    async def list_transactions(self, engagement_id: str) -> list[Transaction]:
        return await self._transactions.list_for_engagement(engagement_id)

    async def scan_for_anomalies(
        self, *, engagement_id: str, structuring_threshold: Decimal = Decimal("10000")
    ) -> list[Anomaly]:
        """Runs the full rule engine over an engagement's current transactions and persists any
        newly-detected anomalies.

        Idempotent with respect to what's already outstanding: a candidate that matches an
        existing, still-``open`` anomaly of the same type (and, where applicable, the same
        transaction) is skipped rather than re-flagged — re-running a scan after importing more
        transactions surfaces only genuinely new findings, not duplicates of ones already awaiting
        review.
        """
        transactions = await self._transactions.list_for_engagement(engagement_id)

        candidates: list[AnomalyCandidate] = []
        benford = detect_benford_deviation(transactions)
        if benford is not None:
            candidates.append(benford)
        candidates.extend(detect_duplicate_payments(transactions))
        candidates.extend(
            detect_threshold_and_round_dollar(
                transactions, structuring_threshold=structuring_threshold
            )
        )

        new_candidates = await self._filter_already_open(engagement_id, candidates)
        if not new_candidates:
            return []

        now = datetime.now(UTC)
        anomalies = [
            Anomaly(
                id=str(uuid.uuid4()),
                engagement_id=engagement_id,
                anomaly_type=c.anomaly_type,
                severity=c.severity,
                status=AnomalyStatus.OPEN,
                detected_at=now,
                transaction_id=c.transaction_id,
                details=c.details,
            )
            for c in new_candidates
        ]
        return await self._anomalies.bulk_create(anomalies)

    async def _filter_already_open(
        self, engagement_id: str, candidates: list[AnomalyCandidate]
    ) -> list[AnomalyCandidate]:
        types_present = {c.anomaly_type for c in candidates}
        already_open: set[tuple[AnomalyType, str | None]] = set()
        for anomaly_type in types_present:
            existing = await self._anomalies.list_open_by_type_for_engagement(
                engagement_id=engagement_id, anomaly_type=anomaly_type
            )
            already_open.update((a.anomaly_type, a.transaction_id) for a in existing)
        return [c for c in candidates if (c.anomaly_type, c.transaction_id) not in already_open]

    async def list_anomalies(self, engagement_id: str) -> list[Anomaly]:
        return await self._anomalies.list_for_engagement(engagement_id)

    async def get_anomaly(self, anomaly_id: str) -> Anomaly:
        anomaly = await self._anomalies.get(anomaly_id)
        if anomaly is None:
            raise NotFoundError(f"Anomaly {anomaly_id} not found.")
        return anomaly

    async def disposition_anomaly(
        self, *, anomaly_id: str, status: AnomalyStatus, reviewed_by: str
    ) -> Anomaly:
        anomaly = await self._anomalies.get(anomaly_id)
        if anomaly is None:
            raise NotFoundError(f"Anomaly {anomaly_id} not found.")
        if anomaly.status != AnomalyStatus.OPEN:
            raise InvalidAnomalyTransitionError(
                f"Anomaly {anomaly_id} is already {anomaly.status.value}, not awaiting disposition."
            )

        reviewed_at = datetime.now(UTC)
        await self._anomalies.update_disposition(
            anomaly_id=anomaly_id, status=status, reviewed_by=reviewed_by, reviewed_at=reviewed_at
        )
        updated = await self._anomalies.get(anomaly_id)
        assert updated is not None  # just written above, in the same transaction

        await self._audit.record(
            engagement_id=anomaly.engagement_id,
            actor_id=reviewed_by,
            action=f"anomaly.{status.value}",
            subject_type="anomaly",
            subject_id=anomaly_id,
            before_state={"status": anomaly.status.value},
            after_state={"status": status.value},
        )
        return updated

    async def compute_risk_scores(self, *, engagement_id: str) -> list[RiskScore]:
        """Runs the full ensemble over an engagement's transactions and persists one composite
        :class:`RiskScore` per transaction.

        Every signal source is best-effort, not a hard prerequisite: an engagement with fewer
        transactions than the ML minimum sample size (``ml_signals.py``) still gets rule-engine-
        and graph-only scores; a transaction with no vendor name has no graph-centrality signal;
        an engagement that never called ``POST .../knowledge-graph/resolve`` gets no
        graph-centrality signal at all (an empty vendor-count map, not an error) — see
        ``combine_signals``'s own docstring for how partial signals are handled. Idempotent:
        recomputing under the same fixed score version refreshes each transaction's row via
        ``RiskScoreRepository.bulk_upsert``.
        """
        transactions = await self._transactions.list_for_engagement(engagement_id)
        if not transactions:
            return []

        anomalies = await self._anomalies.list_for_engagement(engagement_id)
        max_severity_by_transaction: dict[str, AnomalySeverity] = {}
        for anomaly in anomalies:
            if anomaly.transaction_id is None:
                continue
            current = max_severity_by_transaction.get(anomaly.transaction_id)
            if current is None or _SEVERITY_RANK[anomaly.severity] > _SEVERITY_RANK[current]:
                max_severity_by_transaction[anomaly.transaction_id] = anomaly.severity

        features = build_feature_matrix(transactions)
        isolation_forest_scores = compute_isolation_forest_scores(features)
        hdbscan_noise_flags = compute_hdbscan_noise_flags(features)
        vendor_transaction_counts = await self._vendor_centrality.get_vendor_transaction_counts(
            engagement_id=engagement_id
        )

        now = datetime.now(UTC)
        scores = []
        for txn in transactions:
            vendor = normalized_vendor_name(txn)
            vendor_count = vendor_transaction_counts.get(vendor) if vendor is not None else None
            combined = combine_signals(
                rule_engine_max_severity=max_severity_by_transaction.get(txn.id),
                isolation_forest_score=isolation_forest_scores.get(txn.id),
                hdbscan_is_noise=hdbscan_noise_flags.get(txn.id),
                vendor_transaction_count=vendor_count,
            )
            scores.append(
                RiskScore(
                    id=str(uuid.uuid4()),
                    engagement_id=engagement_id,
                    subject_type="transaction",
                    subject_id=txn.id,
                    score=combined.score,
                    score_version=_CURRENT_SCORE_VERSION,
                    contributing_factors=combined.contributing_factors,
                    computed_at=now,
                )
            )
        return await self._risk_scores.bulk_upsert(scores)

    async def list_risk_scores(self, engagement_id: str) -> list[RiskScore]:
        return await self._risk_scores.list_for_engagement(engagement_id)

    async def validate_model(self, engagement_id: str) -> ModelValidationResult:
        """Cross-validated performance of the Isolation Forest / HDBSCAN signals and an ablation
        of the combiner's fixed weights, over this engagement's real, currently-imported
        transactions. See ``model_validation.py``'s module docstring for the ground-truth-proxy
        caveat every field here inherits.
        """
        transactions = await self._transactions.list_for_engagement(engagement_id)
        anomalies = await self._anomalies.list_for_engagement(engagement_id)
        risk_scores = await self._risk_scores.list_for_engagement(engagement_id)

        flagged_ids = {a.transaction_id for a in anomalies if a.transaction_id is not None}
        labels_by_transaction = {txn.id: txn.id in flagged_ids for txn in transactions}

        features = build_feature_matrix(transactions)
        labels = [labels_by_transaction[f.transaction_id] for f in features]

        contributing_factors_by_transaction = {
            score.subject_id: score.contributing_factors
            for score in risk_scores
            if score.subject_type == "transaction"
        }
        baseline_auc, ablation = ablate_combiner(
            contributing_factors_by_transaction, labels_by_transaction
        )

        return ModelValidationResult(
            transaction_count=len(transactions),
            flagged_count=len(flagged_ids),
            isolation_forest=cross_validate_isolation_forest(features, labels),
            hdbscan_stability=hdbscan_stability(features),
            baseline_combined_auc=baseline_auc,
            combiner_ablation=ablation,
        )
