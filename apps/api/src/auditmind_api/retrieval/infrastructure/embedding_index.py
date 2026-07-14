"""Adapter for the ``ChunkVectorIndex`` port: stores and searches ``retrieval.chunk_embeddings``,
the table this context owns (Increment 08, Phase 4 §2).

``upsert_embeddings`` goes through the ORM (``ChunkEmbeddingModel``) — the same convention every
other context uses for a table it owns (e.g. ``ingestion``'s ``PostgresChunkRepository``), unlike
the raw-SQL-only adapters this context uses to read *another* context's table. ``search`` stays
raw SQL despite owning the table, because it joins against ``ingestion.chunks`` to return each
match's text and document id — the same cross-context raw-SQL convention
``search_index.py``/``chunk_text_source.py`` already establish, applied here to a join instead of
a single-table read.
"""

from __future__ import annotations

from typing import Any, cast

from pgvector.utils import Vector
from sqlalchemy import CursorResult, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.retrieval.domain.entities import SearchResult
from auditmind_api.retrieval.infrastructure.models import ChunkEmbeddingModel


class PgVectorChunkIndex:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_embeddings(
        self,
        *,
        engagement_id: str,
        model_id: str,
        embeddings: list[tuple[str, list[float]]],
    ) -> int:
        if not embeddings:
            return 0
        stmt = (
            insert(ChunkEmbeddingModel)
            .values(
                [
                    {
                        "chunk_id": chunk_id,
                        "model_id": model_id,
                        "engagement_id": engagement_id,
                        "embedding": vector,
                    }
                    for chunk_id, vector in embeddings
                ]
            )
            .on_conflict_do_nothing(index_elements=["chunk_id", "model_id"])
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        # `.execute()` on an INSERT statement always returns a CursorResult at runtime; the async
        # session's return type is the more general `Result[Any]` because other statement shapes
        # (e.g. a `select()`) don't have `.rowcount` at all.
        return cast(CursorResult[Any], result).rowcount or 0

    async def search(
        self,
        *,
        engagement_id: str,
        model_id: str,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[SearchResult]:
        # ef_search trades recall for latency at query time only (Phase 6 §7) — a lower value
        # here favors the interactive-search latency target over maximum recall; the offline
        # evaluation harness that would use a higher value is Phase 9, not built yet.
        await self._session.execute(text("SET LOCAL hnsw.ef_search = 40"))
        # `CAST(:x AS vector)` rather than the terser `:x::vector` — SQLAlchemy's `text()` bind
        # parameter parser reads a `:name` immediately followed by `::` as part of the same token,
        # producing a literal `syntax error at or near ":"` at execution time rather than the cast
        # Postgres would otherwise accept (confirmed by hitting exactly that error during testing).
        result = await self._session.execute(
            text(
                "SELECT c.id, c.document_id, c.engagement_id, c.text, "
                "       1 - (e.embedding <=> CAST(:query_embedding AS vector)) AS similarity "
                "FROM retrieval.chunk_embeddings e "
                "JOIN ingestion.chunks c ON c.id = e.chunk_id "
                "WHERE e.engagement_id = :engagement_id AND e.model_id = :model_id "
                "ORDER BY e.embedding <=> CAST(:query_embedding AS vector) "
                "LIMIT :limit"
            ),
            {
                "engagement_id": engagement_id,
                "model_id": model_id,
                "query_embedding": Vector(query_embedding).to_text(),
                "limit": limit,
            },
        )
        return [
            SearchResult(
                chunk_id=str(row.id),
                document_id=str(row.document_id),
                engagement_id=str(row.engagement_id),
                text=row.text,
                rank=float(row.similarity),
            )
            for row in result.all()
        ]
