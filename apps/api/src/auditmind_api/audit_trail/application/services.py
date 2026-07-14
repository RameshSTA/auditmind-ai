"""Application service for the Audit Trail context. No SQL, no HTTP — only coordination of the
port, the same pattern every prior context's service established."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from auditmind_api.audit_trail.domain.entities import ActorType, AuditEvent
from auditmind_api.audit_trail.domain.ports import AuditEventRepository


class AuditTrailService:
    def __init__(self, event_repository: AuditEventRepository) -> None:
        self._events = event_repository

    async def record_event(
        self,
        *,
        engagement_id: str,
        actor_type: ActorType,
        action: str,
        subject_type: str,
        actor_id: str | None = None,
        subject_id: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=str(uuid.uuid4()),
            engagement_id=engagement_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            before_state=before_state,
            after_state=after_state,
            occurred_at=datetime.now(UTC),
        )
        return await self._events.create(event)

    async def list_events(self, engagement_id: str) -> list[AuditEvent]:
        return await self._events.list_for_engagement(engagement_id)
