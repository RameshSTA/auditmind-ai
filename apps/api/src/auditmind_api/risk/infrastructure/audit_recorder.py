"""Adapter for the ``AuditTrailRecorder`` port: writes to ``audit_trail.events`` without importing
``audit_trail``'s ORM model (same raw-SQL boundary convention as
``reporting/infrastructure/audit_recorder.py`` and ``chunk_lookup.py``)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PostgresAuditTrailRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        engagement_id: str,
        actor_id: str,
        action: str,
        subject_type: str,
        subject_id: str,
        before_state: dict[str, Any] | None,
        after_state: dict[str, Any] | None,
    ) -> None:
        await self._session.execute(
            text(
                "INSERT INTO audit_trail.events "
                "(engagement_id, actor_type, actor_id, action, subject_type, subject_id, "
                " before_state, after_state) "
                "VALUES (:engagement_id, 'human', :actor_id, :action, :subject_type, :subject_id, "
                "        :before_state, :after_state)"
            ),
            {
                "engagement_id": engagement_id,
                "actor_id": actor_id,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "before_state": json.dumps(before_state) if before_state is not None else None,
                "after_state": json.dumps(after_state) if after_state is not None else None,
            },
        )
