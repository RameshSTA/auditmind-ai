"""Unit tests for IngestionService (application layer) — against in-memory fakes, no
database or filesystem involved."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from auditmind_api.ingestion.application.services import IngestionService
from auditmind_api.ingestion.domain.entities import Chunk, Document, DocumentStatus


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}

    async def get_by_hash(self, *, engagement_id: str, sha256_hash: str) -> Document | None:
        for doc in self.documents.values():
            if doc.engagement_id == engagement_id and doc.sha256_hash == sha256_hash:
                return doc
        return None

    async def create(self, document: Document) -> Document:
        self.documents[document.id] = document
        return document

    async def update_status(self, *, document_id: str, status: DocumentStatus) -> None:
        existing = self.documents[document_id]
        self.documents[document_id] = Document(
            id=existing.id,
            engagement_id=existing.engagement_id,
            source_connector=existing.source_connector,
            original_filename=existing.original_filename,
            storage_uri=existing.storage_uri,
            sha256_hash=existing.sha256_hash,
            mime_type=existing.mime_type,
            status=status,
            ingested_at=existing.ingested_at,
            ingested_by=existing.ingested_by,
            duplicate_of=existing.duplicate_of,
        )

    async def get(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)

    async def list_for_engagement(self, engagement_id: str) -> list[Document]:
        return [d for d in self.documents.values() if d.engagement_id == engagement_id]


class FakeChunkRepository:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []

    async def bulk_create(self, chunks: list[Chunk]) -> None:
        self.chunks.extend(chunks)

    async def list_for_document(self, document_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.document_id == document_id]


class FakeBlobStorage:
    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}

    async def put(
        self, *, engagement_id: str, document_id: str, filename: str, content: bytes
    ) -> str:
        uri = f"{engagement_id}/{document_id}__{filename}"
        self.stored[uri] = content
        return uri

    async def get(self, storage_uri: str) -> bytes:
        return self.stored[storage_uri]


class FakeParserRouter:
    def __init__(self, text_to_return: str = "extracted text", should_fail: bool = False) -> None:
        self.text_to_return = text_to_return
        self.should_fail = should_fail
        self.calls: list[tuple[str, bytes]] = []

    def parse(self, *, mime_type: str, content: bytes) -> str:
        self.calls.append((mime_type, content))
        if self.should_fail:
            raise ValueError("simulated parsing failure")
        return self.text_to_return


@pytest.fixture
def document_repo() -> FakeDocumentRepository:
    return FakeDocumentRepository()


@pytest.fixture
def chunk_repo() -> FakeChunkRepository:
    return FakeChunkRepository()


@pytest.fixture
def blob_storage() -> FakeBlobStorage:
    return FakeBlobStorage()


async def test_ingest_document_stores_parses_and_chunks_a_new_document(
    document_repo: FakeDocumentRepository,
    chunk_repo: FakeChunkRepository,
    blob_storage: FakeBlobStorage,
) -> None:
    parser = FakeParserRouter(text_to_return="Paragraph one.\n\nParagraph two.")
    service = IngestionService(document_repo, chunk_repo, blob_storage, parser)

    document = await service.ingest_document(
        engagement_id="eng-1",
        filename="notes.txt",
        content=b"raw bytes",
        mime_type="text/plain",
        ingested_by="user-1",
    )

    assert document.status == DocumentStatus.PARSED
    assert document.engagement_id == "eng-1"
    assert len(blob_storage.stored) == 1
    assert len(chunk_repo.chunks) >= 1
    assert parser.calls == [("text/plain", b"raw bytes")]


async def test_ingest_document_returns_existing_document_on_exact_duplicate(
    document_repo: FakeDocumentRepository,
    chunk_repo: FakeChunkRepository,
    blob_storage: FakeBlobStorage,
) -> None:
    parser = FakeParserRouter()
    service = IngestionService(document_repo, chunk_repo, blob_storage, parser)

    first = await service.ingest_document(
        engagement_id="eng-1",
        filename="a.txt",
        content=b"identical content",
        mime_type="text/plain",
        ingested_by="user-1",
    )
    second = await service.ingest_document(
        engagement_id="eng-1",
        filename="a-renamed.txt",  # even a different filename is still the same content
        content=b"identical content",
        mime_type="text/plain",
        ingested_by="user-2",
    )

    assert first.id == second.id
    # The second call never re-stored, re-parsed, or re-chunked — caught before any of that work.
    assert len(blob_storage.stored) == 1
    assert len(parser.calls) == 1


async def test_ingest_document_treats_same_content_in_different_engagements_as_distinct(
    document_repo: FakeDocumentRepository,
    chunk_repo: FakeChunkRepository,
    blob_storage: FakeBlobStorage,
) -> None:
    """Duplicate detection is scoped per engagement — the same file uploaded to two different
    engagements is two distinct documents, not a cross-engagement duplicate, since evidence is
    never implicitly shared across engagement boundaries."""
    parser = FakeParserRouter()
    service = IngestionService(document_repo, chunk_repo, blob_storage, parser)

    doc_a = await service.ingest_document(
        engagement_id="eng-1",
        filename="a.txt",
        content=b"shared content",
        mime_type="text/plain",
        ingested_by="user-1",
    )
    doc_b = await service.ingest_document(
        engagement_id="eng-2",
        filename="a.txt",
        content=b"shared content",
        mime_type="text/plain",
        ingested_by="user-1",
    )

    assert doc_a.id != doc_b.id
    assert len(parser.calls) == 2


async def test_ingest_document_marks_status_failed_and_reraises_on_parsing_error(
    document_repo: FakeDocumentRepository,
    chunk_repo: FakeChunkRepository,
    blob_storage: FakeBlobStorage,
) -> None:
    parser = FakeParserRouter(should_fail=True)
    service = IngestionService(document_repo, chunk_repo, blob_storage, parser)

    with pytest.raises(ValueError, match="simulated parsing failure"):
        await service.ingest_document(
            engagement_id="eng-1",
            filename="bad.pdf",
            content=b"broken",
            mime_type="application/pdf",
            ingested_by="user-1",
        )

    # The document row still exists (upload succeeded) but is marked failed, not silently lost —
    # this is what lets an auditor see "this upload failed" rather than nothing at all.
    documents = list(document_repo.documents.values())
    assert len(documents) == 1
    assert documents[0].status == DocumentStatus.FAILED
    assert len(chunk_repo.chunks) == 0


async def test_list_documents_returns_only_the_requested_engagements_documents(
    document_repo: FakeDocumentRepository,
    chunk_repo: FakeChunkRepository,
    blob_storage: FakeBlobStorage,
) -> None:
    parser = FakeParserRouter()
    service = IngestionService(document_repo, chunk_repo, blob_storage, parser)
    await service.ingest_document(
        engagement_id="eng-1",
        filename="a.txt",
        content=b"a",
        mime_type="text/plain",
        ingested_by="u",
    )
    await service.ingest_document(
        engagement_id="eng-2",
        filename="b.txt",
        content=b"b",
        mime_type="text/plain",
        ingested_by="u",
    )

    result = await service.list_documents("eng-1")

    assert len(result) == 1
    assert result[0].engagement_id == "eng-1"


async def test_list_chunks_returns_chunks_for_the_requested_document_only(
    document_repo: FakeDocumentRepository,
    chunk_repo: FakeChunkRepository,
    blob_storage: FakeBlobStorage,
) -> None:
    parser = FakeParserRouter(text_to_return="Some chunkable text content here.")
    service = IngestionService(document_repo, chunk_repo, blob_storage, parser)
    doc = await service.ingest_document(
        engagement_id="eng-1",
        filename="a.txt",
        content=b"a",
        mime_type="text/plain",
        ingested_by="u",
    )
    # An unrelated chunk from a different (fake) document must never leak into the result.
    chunk_repo.chunks.append(
        Chunk(
            id=str(uuid.uuid4()),
            document_id="some-other-document",
            engagement_id="eng-1",
            chunk_index=0,
            text="unrelated",
            char_start=0,
            char_end=9,
            created_at=datetime.now(UTC),
        )
    )

    result = await service.list_chunks(doc.id)

    assert all(c.document_id == doc.id for c in result)
