"""HTTP routes for the risk bounded context — transactions, anomaly scanning, and risk scoring."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from auditmind_api.identity.domain.entities import EngagementMembership, User
from auditmind_api.identity.interface.dependencies import (
    get_current_db_user,
    require_engagement_member,
)
from auditmind_api.risk.application.model_validation import ModelValidationResult
from auditmind_api.risk.application.services import RiskService
from auditmind_api.risk.domain.entities import Anomaly, AnomalyStatus, RiskScore, Transaction
from auditmind_api.risk.interface.dependencies import get_risk_service
from auditmind_api.risk.interface.schemas import (
    DispositionAnomalyRequest,
    ImportTransactionsRequest,
    ScanRequest,
    transaction_record_to_service_input,
)
from auditmind_api.shared.metrics import anomalies_detected_total, risk_scores_computed_total
from auditmind_api.shared.roles import (
    CAN_AUTHOR_FINDINGS,
    CAN_DISPOSITION_FINDINGS,
    CAN_READ_FINDINGS,
)

router = APIRouter(tags=["risk"])


def _transaction_response(transaction: Transaction) -> dict[str, object]:
    return {
        "id": transaction.id,
        "engagement_id": transaction.engagement_id,
        "source_system": transaction.source_system,
        "amount": str(transaction.amount),
        "currency": transaction.currency,
        "transaction_date": transaction.transaction_date.isoformat(),
    }


def _anomaly_response(anomaly: Anomaly) -> dict[str, object]:
    return {
        "id": anomaly.id,
        "engagement_id": anomaly.engagement_id,
        "anomaly_type": anomaly.anomaly_type.value,
        "severity": anomaly.severity.value,
        "status": anomaly.status.value,
        "transaction_id": anomaly.transaction_id,
        "details": anomaly.details,
        "reviewed_by": anomaly.reviewed_by,
    }


def _risk_score_response(score: RiskScore) -> dict[str, object]:
    return {
        "id": score.id,
        "engagement_id": score.engagement_id,
        "subject_type": score.subject_type,
        "subject_id": score.subject_id,
        "score": str(score.score),
        "score_version": score.score_version,
        "contributing_factors": score.contributing_factors,
        "computed_at": score.computed_at.isoformat(),
    }


@router.post("/v1/engagements/{engagement_id}/transactions", status_code=201)
async def import_transactions(
    body: ImportTransactionsRequest,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    db_user: User = Depends(get_current_db_user),
    risk_service: RiskService = Depends(get_risk_service),
) -> list[dict[str, object]]:
    """Bulk-imports transaction records — the only path transaction data enters the platform
    until a real ERP connector exists.
    """
    records = [transaction_record_to_service_input(r) for r in body.transactions]
    transactions = await risk_service.import_transactions(
        engagement_id=membership.engagement_id, records=records, created_by=db_user.id
    )
    return [_transaction_response(t) for t in transactions]


@router.get("/v1/engagements/{engagement_id}/transactions")
async def list_transactions(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> list[dict[str, object]]:
    transactions = await risk_service.list_transactions(membership.engagement_id)
    return [_transaction_response(t) for t in transactions]


@router.post("/v1/engagements/{engagement_id}/risk/scan", status_code=201)
async def scan_for_anomalies(
    body: ScanRequest = ScanRequest(),
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> list[dict[str, object]]:
    """Runs the rule engine (Benford's Law, duplicate-payment matching, threshold/round-dollar
    detection) over the engagement's current transactions.

    Idempotent with respect to what's already open — see ``RiskService.scan_for_anomalies``'s
    docstring. Returns only the newly-created anomalies, not the full outstanding set (use
    ``GET .../anomalies`` for that).
    """
    anomalies = await risk_service.scan_for_anomalies(
        engagement_id=membership.engagement_id,
        structuring_threshold=body.structuring_threshold,
    )
    for anomaly in anomalies:
        anomalies_detected_total.add(1, {"anomaly_type": anomaly.anomaly_type.value})
    return [_anomaly_response(a) for a in anomalies]


@router.get("/v1/engagements/{engagement_id}/anomalies")
async def list_anomalies(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> list[dict[str, object]]:
    anomalies = await risk_service.list_anomalies(membership.engagement_id)
    return [_anomaly_response(a) for a in anomalies]


@router.get("/v1/engagements/{engagement_id}/anomalies/{anomaly_id}")
async def get_anomaly(
    anomaly_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> dict[str, object]:
    anomaly = await risk_service.get_anomaly(anomaly_id)
    return _anomaly_response(anomaly)


@router.post("/v1/engagements/{engagement_id}/anomalies/{anomaly_id}/disposition")
async def disposition_anomaly(
    anomaly_id: str,
    body: DispositionAnomalyRequest,
    membership: EngagementMembership = Depends(
        require_engagement_member(*CAN_DISPOSITION_FINDINGS)
    ),
    db_user: User = Depends(get_current_db_user),
    risk_service: RiskService = Depends(get_risk_service),
) -> dict[str, object]:
    """The human-review gate for anomalies — restricted to Auditor/Fraud Analyst."""
    anomaly = await risk_service.disposition_anomaly(
        anomaly_id=anomaly_id,
        status=AnomalyStatus(body.status),
        reviewed_by=db_user.id,
    )
    return _anomaly_response(anomaly)


@router.post("/v1/engagements/{engagement_id}/risk/score", status_code=201)
async def compute_risk_scores(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> list[dict[str, object]]:
    """Runs the full fraud-scoring ensemble — rule engine, Isolation Forest, HDBSCAN cohort
    clustering, and graph centrality — combined into one composite score per transaction. For
    best results, run ``POST .../knowledge-graph/resolve`` first so the graph-centrality signal
    has vendor data to read; scoring still runs without it, just with one fewer signal (see
    ``RiskService.compute_risk_scores``'s docstring). Idempotent: recomputing refreshes each
    transaction's score in place rather than accumulating duplicates."""
    scores = await risk_service.compute_risk_scores(engagement_id=membership.engagement_id)
    if scores:
        risk_scores_computed_total.add(len(scores))
    return [_risk_score_response(s) for s in scores]


@router.get("/v1/engagements/{engagement_id}/risk-scores")
async def list_risk_scores(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> list[dict[str, object]]:
    scores = await risk_service.list_risk_scores(membership.engagement_id)
    return [_risk_score_response(s) for s in scores]


def _model_validation_response(result: ModelValidationResult) -> dict[str, object]:
    return {
        "transaction_count": result.transaction_count,
        "flagged_count": result.flagged_count,
        "isolation_forest": (
            {
                "roc_auc_mean": result.isolation_forest.roc_auc_mean,
                "roc_auc_std": result.isolation_forest.roc_auc_std,
                "precision_at_p90_mean": result.isolation_forest.precision_at_p90_mean,
                "recall_at_p90_mean": result.isolation_forest.recall_at_p90_mean,
                "fold_count": result.isolation_forest.fold_count,
            }
            if result.isolation_forest
            else None
        ),
        "hdbscan_stability": (
            {
                "noise_fraction_mean": result.hdbscan_stability.noise_fraction_mean,
                "noise_fraction_std": result.hdbscan_stability.noise_fraction_std,
                "cluster_count_mean": result.hdbscan_stability.cluster_count_mean,
                "resample_count": result.hdbscan_stability.resample_count,
            }
            if result.hdbscan_stability
            else None
        ),
        "baseline_combined_auc": result.baseline_combined_auc,
        "combiner_ablation": [
            {
                "signal_name": entry.signal_name,
                "auc_without_signal": entry.auc_without_signal,
                "delta": entry.delta,
            }
            for entry in result.combiner_ablation
        ],
    }


@router.get("/v1/engagements/{engagement_id}/risk/model-validation")
async def get_model_validation(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    risk_service: RiskService = Depends(get_risk_service),
) -> dict[str, object]:
    """Cross-validated performance of the Isolation Forest / HDBSCAN signals plus a weighted-
    combiner ablation, computed live over this engagement's real transactions/anomalies/risk
    scores (``RiskService.validate_model``). Every field degrades to ``null`` rather than a
    fabricated number when there isn't yet enough real data to compute it meaningfully."""
    result = await risk_service.validate_model(membership.engagement_id)
    return _model_validation_response(result)
