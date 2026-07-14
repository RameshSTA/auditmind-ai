"""Postgres-backed implementations of the investigations repository ports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.investigations.domain.entities import (
    Investigation,
    InvestigationItem,
    InvestigationStatus,
    InvestigationSubjectType,
)
from auditmind_api.investigations.infrastructure.models import (
    InvestigationItemModel,
    InvestigationModel,
)


def _to_investigation_entity(model: InvestigationModel) -> Investigation:
    return Investigation(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        title=model.title,
        description=model.description,
        status=InvestigationStatus(model.status),
        created_by=str(model.created_by),
        created_at=model.created_at,
        closed_by=str(model.closed_by) if model.closed_by else None,
        closed_at=model.closed_at,
        conclusion=model.conclusion,
    )


def _to_item_entity(model: InvestigationItemModel) -> InvestigationItem:
    return InvestigationItem(
        id=str(model.id),
        investigation_id=str(model.investigation_id),
        engagement_id=str(model.engagement_id),
        subject_type=InvestigationSubjectType(model.subject_type),
        subject_id=str(model.subject_id),
        added_by=str(model.added_by),
        added_at=model.added_at,
        note=model.note,
    )


class PostgresInvestigationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, investigation: Investigation) -> Investigation:
        model = InvestigationModel(
            id=investigation.id,
            engagement_id=investigation.engagement_id,
            title=investigation.title,
            description=investigation.description,
            status=investigation.status.value,
            created_by=investigation.created_by,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_investigation_entity(model)

    async def get(self, investigation_id: str) -> Investigation | None:
        result = await self._session.execute(
            select(InvestigationModel).where(InvestigationModel.id == investigation_id)
        )
        model = result.scalars().first()
        return _to_investigation_entity(model) if model else None

    async def list_for_engagement(self, engagement_id: str) -> list[Investigation]:
        result = await self._session.execute(
            select(InvestigationModel).where(InvestigationModel.engagement_id == engagement_id)
        )
        return [_to_investigation_entity(m) for m in result.scalars().all()]

    async def close(
        self,
        *,
        investigation_id: str,
        closed_by: str,
        closed_at: datetime,
        conclusion: str,
    ) -> None:
        result = await self._session.execute(
            select(InvestigationModel).where(InvestigationModel.id == investigation_id)
        )
        model = result.scalar_one()
        model.status = InvestigationStatus.CLOSED.value
        model.closed_by = uuid.UUID(closed_by)
        model.closed_at = closed_at
        model.conclusion = conclusion
        await self._session.flush()


class PostgresInvestigationItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, item: InvestigationItem) -> InvestigationItem:
        model = InvestigationItemModel(
            id=item.id,
            investigation_id=item.investigation_id,
            engagement_id=item.engagement_id,
            subject_type=item.subject_type.value,
            subject_id=item.subject_id,
            added_by=item.added_by,
            note=item.note,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_item_entity(model)

    async def remove(self, item_id: str) -> None:
        result = await self._session.execute(
            select(InvestigationItemModel).where(InvestigationItemModel.id == item_id)
        )
        model = result.scalar_one()
        await self._session.delete(model)
        await self._session.flush()

    async def list_for_investigation(self, investigation_id: str) -> list[InvestigationItem]:
        result = await self._session.execute(
            select(InvestigationItemModel).where(
                InvestigationItemModel.investigation_id == investigation_id
            )
        )
        return [_to_item_entity(m) for m in result.scalars().all()]
