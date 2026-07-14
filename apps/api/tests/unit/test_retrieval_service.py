"""Unit tests for RetrievalService (Phase 3 §1 application layer) — against in-memory fakes."""

from __future__ import annotations

from auditmind_api.retrieval.application.services import RetrievalService
from auditmind_api.retrieval.domain.entities import ChunkText, SearchResult


class FakeChunkSearchIndex:
    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple[str, str, int]] = []

    async def search(
        self, *, engagement_id: str, query: str, limit: int = 20
    ) -> list[SearchResult]:
        self.calls.append((engagement_id, query, limit))
        return self.results


class FakeChunkTextSource:
    def __init__(self, chunks: list[ChunkText] | None = None) -> None:
        self.chunks = chunks or []
        self.calls: list[str] = []

    async def list_chunk_texts(self, *, document_id: str) -> list[ChunkText]:
        self.calls.append(document_id)
        return self.chunks


class FakeEmbeddingGenerator:
    model_id = "fake-embedder-v1"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(len(t))] for t in texts]


class FakeChunkVectorIndex:
    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results or []
        self.upsert_calls: list[tuple[str, str, list[tuple[str, list[float]]]]] = []
        self.search_calls: list[tuple[str, str, list[float], int]] = []

    async def upsert_embeddings(
        self,
        *,
        engagement_id: str,
        model_id: str,
        embeddings: list[tuple[str, list[float]]],
    ) -> int:
        self.upsert_calls.append((engagement_id, model_id, embeddings))
        return len(embeddings)

    async def search(
        self,
        *,
        engagement_id: str,
        model_id: str,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[SearchResult]:
        self.search_calls.append((engagement_id, model_id, query_embedding, limit))
        return self.results


def _make_service(
    *,
    chunk_search_index: FakeChunkSearchIndex | None = None,
    chunk_text_source: FakeChunkTextSource | None = None,
    embedding_generator: FakeEmbeddingGenerator | None = None,
    chunk_vector_index: FakeChunkVectorIndex | None = None,
) -> RetrievalService:
    return RetrievalService(
        chunk_search_index=chunk_search_index or FakeChunkSearchIndex(),
        chunk_text_source=chunk_text_source or FakeChunkTextSource(),
        embedding_generator=embedding_generator or FakeEmbeddingGenerator(),
        chunk_vector_index=chunk_vector_index or FakeChunkVectorIndex(),
    )


async def test_search_chunks_delegates_to_the_index_with_given_arguments() -> None:
    expected = [
        SearchResult(
            chunk_id="c1", document_id="d1", engagement_id="eng-1", text="matched", rank=0.5
        )
    ]
    index = FakeChunkSearchIndex(results=expected)
    service = _make_service(chunk_search_index=index)

    results = await service.search_chunks(engagement_id="eng-1", query="fraud", limit=10)

    assert results == expected
    assert index.calls == [("eng-1", "fraud", 10)]


async def test_search_chunks_defaults_limit_to_twenty() -> None:
    index = FakeChunkSearchIndex()
    service = _make_service(chunk_search_index=index)

    await service.search_chunks(engagement_id="eng-1", query="fraud")

    assert index.calls == [("eng-1", "fraud", 20)]


async def test_embed_document_embeds_and_stores_every_chunks_text() -> None:
    chunks = [ChunkText(chunk_id="c1", text="hello"), ChunkText(chunk_id="c2", text="world!")]
    text_source = FakeChunkTextSource(chunks=chunks)
    generator = FakeEmbeddingGenerator()
    vector_index = FakeChunkVectorIndex()
    service = _make_service(
        chunk_text_source=text_source,
        embedding_generator=generator,
        chunk_vector_index=vector_index,
    )

    embedded_count = await service.embed_document(engagement_id="eng-1", document_id="doc-1")

    assert text_source.calls == ["doc-1"]
    assert generator.calls == [["hello", "world!"]]
    assert vector_index.upsert_calls == [
        ("eng-1", "fake-embedder-v1", [("c1", [5.0]), ("c2", [6.0])])
    ]
    assert embedded_count == 2


async def test_embed_document_with_no_chunks_embeds_nothing() -> None:
    generator = FakeEmbeddingGenerator()
    vector_index = FakeChunkVectorIndex()
    service = _make_service(
        chunk_text_source=FakeChunkTextSource(chunks=[]),
        embedding_generator=generator,
        chunk_vector_index=vector_index,
    )

    embedded_count = await service.embed_document(engagement_id="eng-1", document_id="doc-1")

    assert embedded_count == 0
    assert generator.calls == []  # never calls the model for an empty chunk list
    assert vector_index.upsert_calls == []


async def test_search_semantic_embeds_the_query_and_delegates_to_the_vector_index() -> None:
    expected = [
        SearchResult(
            chunk_id="c1", document_id="d1", engagement_id="eng-1", text="matched", rank=0.9
        )
    ]
    generator = FakeEmbeddingGenerator()
    vector_index = FakeChunkVectorIndex(results=expected)
    service = _make_service(embedding_generator=generator, chunk_vector_index=vector_index)

    results = await service.search_semantic(engagement_id="eng-1", query="fraud", limit=5)

    assert results == expected
    assert generator.calls == [["fraud"]]
    assert vector_index.search_calls == [("eng-1", "fake-embedder-v1", [5.0], 5)]


async def test_search_semantic_defaults_limit_to_twenty() -> None:
    vector_index = FakeChunkVectorIndex()
    service = _make_service(chunk_vector_index=vector_index)

    await service.search_semantic(engagement_id="eng-1", query="fraud")

    assert vector_index.search_calls[0][3] == 20
