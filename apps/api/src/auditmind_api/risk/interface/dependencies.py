"""FastAPI dependencies wiring the Risk context into the request lifecycle."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.risk.application.services import RiskService
from auditmind_api.risk.infrastructure.audit_recorder import PostgresAuditTrailRecorder
from auditmind_api.risk.infrastructure.neo4j_vendor_centrality import (
    Neo4jVendorCentralitySource,
)
from auditmind_api.risk.infrastructure.repository import (
    PostgresAnomalyRepository,
    PostgresRiskScoreRepository,
    PostgresTransactionRepository,
)
from auditmind_api.shared.database import get_db_session
from auditmind_api.shared.neo4j import get_neo4j_driver


def get_risk_service(session: AsyncSession = Depends(get_db_session)) -> RiskService:
    return RiskService(
        transaction_repository=PostgresTransactionRepository(session),
        anomaly_repository=PostgresAnomalyRepository(session),
        audit_recorder=PostgresAuditTrailRecorder(session),
        risk_score_repository=PostgresRiskScoreRepository(session),
        vendor_centrality_source=Neo4jVendorCentralitySource(get_neo4j_driver()),
    )
