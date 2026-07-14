"""FastAPI dependencies wiring the Retrieval context into the request lifecycle."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.retrieval.application.services import RetrievalService
from auditmind_api.retrieval.domain.ports import EmbeddingGenerator
from auditmind_api.retrieval.infrastructure.bge_m3_embedding_generator import (
    BgeM3EmbeddingGenerator,
)
from auditmind_api.retrieval.infrastructure.chunk_text_source import PostgresChunkTextSource
from auditmind_api.retrieval.infrastructure.embedding_index import PgVectorChunkIndex
from auditmind_api.retrieval.infrastructure.search_index import PostgresFullTextSearchIndex
from auditmind_api.shared.database import get_db_session


@lru_cache(maxsize=1)
def get_embedding_generator() -> EmbeddingGenerator:
    """Process-wide singleton, not one per request — the underlying model load
    (``bge_m3_embedding_generator._load_model``) is itself cached, but constructing a new
    ``BgeM3EmbeddingGenerator`` per request costs nothing extra either way; cached here so a test
    can override this exact callable with ``app.dependency_overrides`` to swap in a fake without
    ever importing (let alone loading) the real model."""
    return BgeM3EmbeddingGenerator()


def get_retrieval_service(
    session: AsyncSession = Depends(get_db_session),
    embedding_generator: EmbeddingGenerator = Depends(get_embedding_generator),
) -> RetrievalService:
    return RetrievalService(
        chunk_search_index=PostgresFullTextSearchIndex(session),
        chunk_text_source=PostgresChunkTextSource(session),
        embedding_generator=embedding_generator,
        chunk_vector_index=PgVectorChunkIndex(session),
    )
