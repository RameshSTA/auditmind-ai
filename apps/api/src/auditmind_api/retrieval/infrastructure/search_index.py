"""Adapter for the ``ChunkSearchIndex`` port: Postgres full-text search over ``ingestion.chunks``
without importing ``ingestion``'s ORM model (same raw-SQL boundary convention as
``reporting/infrastructure/chunk_lookup.py``).

Reads ``search_vector`` — a generated, GIN-indexed ``tsvector`` column Increment 07's migration
adds to ``ingestion.chunks`` (``ingestion`` still owns the table; this context only queries it).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.retrieval.domain.entities import SearchResult


class PostgresFullTextSearchIndex:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self, *, engagement_id: str, query: str, limit: int = 20
    ) -> list[SearchResult]:
        result = await self._session.execute(
            text(
                "SELECT id, document_id, engagement_id, text, "
                "       ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) AS rank "
                "FROM ingestion.chunks "
                "WHERE engagement_id = :engagement_id "
                "  AND search_vector @@ websearch_to_tsquery('english', :query) "
                "ORDER BY rank DESC "
                "LIMIT :limit"
            ),
            {"engagement_id": engagement_id, "query": query, "limit": limit},
        )
        return [
            SearchResult(
                chunk_id=str(row.id),
                document_id=str(row.document_id),
                engagement_id=str(row.engagement_id),
                text=row.text,
                rank=float(row.rank),
            )
            for row in result.all()
        ]
