"""Adapter for the ``ChunkTextSource`` port: reads a document's chunks' text from
``ingestion.chunks`` without importing ``ingestion``'s ORM model (same raw-SQL boundary convention
as ``reporting/infrastructure/chunk_lookup.py`` and this context's own ``search_index.py``).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.retrieval.domain.entities import ChunkText


class PostgresChunkTextSource:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_chunk_texts(self, *, document_id: str) -> list[ChunkText]:
        result = await self._session.execute(
            text(
                "SELECT id, text FROM ingestion.chunks "
                "WHERE document_id = :document_id "
                "ORDER BY chunk_index"
            ),
            {"document_id": document_id},
        )
        return [ChunkText(chunk_id=str(row.id), text=row.text) for row in result.all()]
