"""Adapter ports (Phase 3 §1) for the Retrieval context.

``ChunkSearchIndex`` and ``ChunkTextSource`` are the two things this context needs from
``ingestion``: a way to run a keyword search over its chunks, and a way to read a document's
chunks' raw text to embed them. Each is this context's own minimal protocol, not a shared import —
the same ``ChunkLookup``/``AuditTrailRecorder`` convention Increments 04 and 06 established: each
bounded context defines the shape of what it needs from another context, never the other context's
own port or ORM model.

``EmbeddingGenerator`` and ``ChunkVectorIndex`` are this context's own concerns — the first talks
to a local BGE-M3 model, the second talks to a table this context owns
(``retrieval.chunk_embeddings``, Increment 08). Both are ports (not called directly) for the same
reason ``ChunkSearchIndex`` is: the application layer stays testable against fakes, and the real
model / real pgvector-backed adapter is a swap, not a redesign, if either changes.
"""

from __future__ import annotations

from typing import Protocol

from auditmind_api.retrieval.domain.entities import ChunkText, SearchResult


class ChunkSearchIndex(Protocol):
    async def search(
        self, *, engagement_id: str, query: str, limit: int = 20
    ) -> list[SearchResult]: ...


class ChunkTextSource(Protocol):
    async def list_chunk_texts(self, *, document_id: str) -> list[ChunkText]: ...


class EmbeddingGenerator(Protocol):
    """Turns text into dense vectors. ``model_id`` identifies which model produced them — stored
    alongside every embedding (Phase 4 §2's composite primary key) so a model upgrade is a new
    column of data, never a destructive overwrite of the old model's embeddings."""

    model_id: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChunkVectorIndex(Protocol):
    async def upsert_embeddings(
        self,
        *,
        engagement_id: str,
        model_id: str,
        embeddings: list[tuple[str, list[float]]],
    ) -> int:
        """Stores ``(chunk_id, vector)`` pairs for the given model. Idempotent: re-embedding a
        chunk already embedded by this exact model is a no-op for that row (Phase 4 §2's
        composite primary key makes this a natural ``ON CONFLICT DO NOTHING``, the same
        idempotency discipline Increment 05's anomaly scan already established). Returns the
        number of rows actually inserted, for callers that want to know."""
        ...

    async def search(
        self,
        *,
        engagement_id: str,
        model_id: str,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[SearchResult]: ...
