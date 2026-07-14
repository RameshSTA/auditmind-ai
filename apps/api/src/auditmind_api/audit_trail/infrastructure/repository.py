"""Postgres-backed implementation of the audit-event repository port."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.audit_trail.domain.entities import ActorType, AuditEvent
from auditmind_api.audit_trail.infrastructure.models import AuditEventModel


def _to_entity(model: AuditEventModel) -> AuditEvent:
    return AuditEvent(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        actor_type=ActorType(model.actor_type),
        actor_id=str(model.actor_id) if model.actor_id else None,
        action=model.action,
        subject_type=model.subject_type,
        subject_id=str(model.subject_id) if model.subject_id else None,
        before_state=model.before_state,
        after_state=model.after_state,
        occurred_at=model.occurred_at,
    )


class PostgresAuditEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: AuditEvent) -> AuditEvent:
        model = AuditEventModel(
            id=event.id,
            engagement_id=event.engagement_id,
            actor_type=event.actor_type.value,
            actor_id=event.actor_id,
            action=event.action,
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            before_state=event.before_state,
            after_state=event.after_state,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_entity(model)

    async def list_for_engagement(self, engagement_id: str) -> list[AuditEvent]:
        result = await self._session.execute(
            select(AuditEventModel)
            .where(AuditEventModel.engagement_id == engagement_id)
            .order_by(AuditEventModel.occurred_at)
        )
        return [_to_entity(m) for m in result.scalars().all()]
