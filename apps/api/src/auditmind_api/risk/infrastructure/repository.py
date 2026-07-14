"""Postgres-backed implementations of the risk repository ports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.risk.domain.entities import (
    Anomaly,
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    RiskScore,
    Transaction,
)
from auditmind_api.risk.infrastructure.models import (
    AnomalyModel,
    RiskScoreModel,
    TransactionModel,
)


def _to_transaction_entity(model: TransactionModel) -> Transaction:
    return Transaction(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        source_system=model.source_system,
        amount=model.amount,
        currency=model.currency,
        transaction_date=model.transaction_date,
        raw_payload=model.raw_payload,
        created_by=str(model.created_by),
        created_at=model.created_at,
    )


def _to_risk_score_entity(model: RiskScoreModel) -> RiskScore:
    return RiskScore(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        subject_type=model.subject_type,
        subject_id=str(model.subject_id),
        score=model.score,
        score_version=model.score_version,
        contributing_factors=model.contributing_factors,
        computed_at=model.computed_at,
    )


def _to_anomaly_entity(model: AnomalyModel) -> Anomaly:
    return Anomaly(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        anomaly_type=AnomalyType(model.anomaly_type),
        severity=AnomalySeverity(model.severity),
        status=AnomalyStatus(model.status),
        detected_at=model.detected_at,
        transaction_id=str(model.transaction_id) if model.transaction_id else None,
        details=model.details,
        reviewed_by=str(model.reviewed_by) if model.reviewed_by else None,
        reviewed_at=model.reviewed_at,
    )


class PostgresTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, transactions: list[Transaction]) -> list[Transaction]:
        # Domain entities carry string UUIDs; the ORM columns are UUID(as_uuid=True). Passing a
        # raw string through on a multi-row INSERT...RETURNING breaks SQLAlchemy's
        # insertmanyvalues sentinel matching (it can't reconcile the string it sent with the
        # uuid.UUID postgres/asyncpg hands back), raising InvalidRequestError at flush time —
        # converting here, at the persistence boundary, is the same fix `update_disposition`
        # already applies to `reviewed_by`.
        models = [
            TransactionModel(
                id=uuid.UUID(t.id),
                engagement_id=uuid.UUID(t.engagement_id),
                source_system=t.source_system,
                amount=t.amount,
                currency=t.currency,
                transaction_date=t.transaction_date,
                raw_payload=t.raw_payload,
                created_by=uuid.UUID(t.created_by),
            )
            for t in transactions
        ]
        self._session.add_all(models)
        await self._session.flush()
        for model in models:
            await self._session.refresh(model)
        return [_to_transaction_entity(m) for m in models]

    async def list_for_engagement(self, engagement_id: str) -> list[Transaction]:
        result = await self._session.execute(
            select(TransactionModel).where(TransactionModel.engagement_id == engagement_id)
        )
        return [_to_transaction_entity(m) for m in result.scalars().all()]


class PostgresAnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, anomalies: list[Anomaly]) -> list[Anomaly]:
        # Same string-vs-uuid.UUID sentinel-matching fix as
        # PostgresTransactionRepository.bulk_create.
        models = [
            AnomalyModel(
                id=uuid.UUID(a.id),
                engagement_id=uuid.UUID(a.engagement_id),
                transaction_id=uuid.UUID(a.transaction_id) if a.transaction_id else None,
                anomaly_type=a.anomaly_type.value,
                severity=a.severity.value,
                status=a.status.value,
                details=a.details,
            )
            for a in anomalies
        ]
        self._session.add_all(models)
        await self._session.flush()
        for model in models:
            await self._session.refresh(model)
        return [_to_anomaly_entity(m) for m in models]

    async def list_for_engagement(self, engagement_id: str) -> list[Anomaly]:
        result = await self._session.execute(
            select(AnomalyModel).where(AnomalyModel.engagement_id == engagement_id)
        )
        return [_to_anomaly_entity(m) for m in result.scalars().all()]

    async def get(self, anomaly_id: str) -> Anomaly | None:
        result = await self._session.execute(
            select(AnomalyModel).where(AnomalyModel.id == anomaly_id)
        )
        model = result.scalars().first()
        return _to_anomaly_entity(model) if model else None

    async def list_open_by_type_for_engagement(
        self, *, engagement_id: str, anomaly_type: AnomalyType
    ) -> list[Anomaly]:
        result = await self._session.execute(
            select(AnomalyModel).where(
                AnomalyModel.engagement_id == engagement_id,
                AnomalyModel.anomaly_type == anomaly_type.value,
                AnomalyModel.status == AnomalyStatus.OPEN.value,
            )
        )
        return [_to_anomaly_entity(m) for m in result.scalars().all()]

    async def update_disposition(
        self, *, anomaly_id: str, status: AnomalyStatus, reviewed_by: str, reviewed_at: datetime
    ) -> None:
        result = await self._session.execute(
            select(AnomalyModel).where(AnomalyModel.id == anomaly_id)
        )
        model = result.scalar_one()
        model.status = status.value
        model.reviewed_by = uuid.UUID(reviewed_by)
        model.reviewed_at = reviewed_at
        await self._session.flush()


class PostgresRiskScoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_upsert(self, scores: list[RiskScore]) -> list[RiskScore]:
        if not scores:
            return []
        # Same string-vs-uuid.UUID sentinel-matching fix as
        # PostgresTransactionRepository.bulk_create.
        insert_stmt = insert(RiskScoreModel).values(
            [
                {
                    "id": uuid.UUID(s.id),
                    "engagement_id": uuid.UUID(s.engagement_id),
                    "subject_type": s.subject_type,
                    "subject_id": uuid.UUID(s.subject_id),
                    "score": s.score,
                    "score_version": s.score_version,
                    "contributing_factors": s.contributing_factors,
                }
                for s in scores
            ]
        )
        # Refreshes an existing (subject_type, subject_id, score_version) row's score/factors/
        # timestamp rather than erroring or accumulating a duplicate — the migration's own
        # unique constraint is what this upsert targets.
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["subject_type", "subject_id", "score_version"],
            set_={
                "score": insert_stmt.excluded.score,
                "contributing_factors": insert_stmt.excluded.contributing_factors,
                "computed_at": func.now(),
            },
        ).returning(RiskScoreModel)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return [_to_risk_score_entity(row) for row in result.scalars().all()]

    async def list_for_engagement(self, engagement_id: str) -> list[RiskScore]:
        result = await self._session.execute(
            select(RiskScoreModel).where(RiskScoreModel.engagement_id == engagement_id)
        )
        return [_to_risk_score_entity(m) for m in result.scalars().all()]
