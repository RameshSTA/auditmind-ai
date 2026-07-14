"""Application service for the Retrieval context (Phase 6 §10's keyword leg, §6/§7's
vector-embedding leg). No SQL, no HTTP, no model-loading — only coordination of ports, the same
pattern every prior context's service established."""

from __future__ import annotations

from auditmind_api.retrieval.domain.entities import SearchResult
from auditmind_api.retrieval.domain.ports import (
    ChunkSearchIndex,
    ChunkTextSource,
    ChunkVectorIndex,
    EmbeddingGenerator,
)


class RetrievalService:
    def __init__(
        self,
        chunk_search_index: ChunkSearchIndex,
        chunk_text_source: ChunkTextSource,
        embedding_generator: EmbeddingGenerator,
        chunk_vector_index: ChunkVectorIndex,
    ) -> None:
        self._index = chunk_search_index
        self._chunk_text_source = chunk_text_source
        self._embedding_generator = embedding_generator
        self._vector_index = chunk_vector_index

    async def search_chunks(
        self, *, engagement_id: str, query: str, limit: int = 20
    ) -> list[SearchResult]:
        return await self._index.search(engagement_id=engagement_id, query=query, limit=limit)

    async def embed_document(self, *, engagement_id: str, document_id: str) -> int:
        """Generates and stores embeddings for every chunk of a document (Phase 6 §6).

        Synchronous with the request, not the "asynchronous batch job" Phase 6 §6 designs around
        — there is no job queue yet to run it on (see the increment doc's deferred section). A
        document with no chunks (e.g. still ``received``, not yet parsed) embeds zero rows rather
        than erroring; the caller can re-trigger once ingestion completes.
        """
        chunks = await self._chunk_text_source.list_chunk_texts(document_id=document_id)
        if not chunks:
            return 0
        vectors = await self._embedding_generator.embed([c.text for c in chunks])
        pairs = [(chunk.chunk_id, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
        return await self._vector_index.upsert_embeddings(
            engagement_id=engagement_id,
            model_id=self._embedding_generator.model_id,
            embeddings=pairs,
        )

    async def search_semantic(
        self, *, engagement_id: str, query: str, limit: int = 20
    ) -> list[SearchResult]:
        """The vector-embedding leg's search — embeds the query with the same model every stored
        chunk embedding used, then ranks by cosine similarity (Phase 6 §7)."""
        [query_embedding] = await self._embedding_generator.embed([query])
        return await self._vector_index.search(
            engagement_id=engagement_id,
            model_id=self._embedding_generator.model_id,
            query_embedding=query_embedding,
            limit=limit,
        )
