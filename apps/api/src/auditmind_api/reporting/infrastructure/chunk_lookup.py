"""Adapter for the ``ChunkLookup`` port: reads ``ingestion.chunks`` without importing
``ingestion``'s ORM model — a bounded context depends on another's *schema*, at most, never its
infrastructure classes; the same table-path-string convention used for foreign keys in
``reporting/infrastructure/models.py`` applies here to a plain read query too.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PostgresChunkLookup:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_engagement_id(self, chunk_id: str) -> str | None:
        result = await self._session.execute(
            text("SELECT engagement_id FROM ingestion.chunks WHERE id = :chunk_id"),
            {"chunk_id": chunk_id},
        )
        row = result.first()
        return str(row[0]) if row else None
