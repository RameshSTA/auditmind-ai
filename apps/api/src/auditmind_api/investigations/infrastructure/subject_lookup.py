"""Adapter for the ``InvestigationSubjectLookup`` port: reads ``reporting.findings`` /
``risk.anomalies`` / ``risk.transactions`` without importing those contexts' ORM models — a
bounded context depends on another's *schema*, at most, never its infrastructure classes; the
same table-path-string convention ``reporting.PostgresChunkLookup`` established for a plain
read query."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TABLE_BY_SUBJECT_TYPE = {
    "finding": "reporting.findings",
    "anomaly": "risk.anomalies",
    "transaction": "risk.transactions",
}


class PostgresInvestigationSubjectLookup:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_engagement_id(self, subject_type: str, subject_id: str) -> str | None:
        table = _TABLE_BY_SUBJECT_TYPE.get(subject_type)
        if table is None:
            return None
        result = await self._session.execute(
            text(f"SELECT engagement_id FROM {table} WHERE id = :subject_id"),
            {"subject_id": subject_id},
        )
        row = result.first()
        return str(row[0]) if row else None
