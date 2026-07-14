"""Repository and adapter ports for the Ingestion context.

The application layer depends on these interfaces; infrastructure provides the only
implementations. No SQLAlchemy, no filesystem, no parsing-library import belongs here.
"""

from __future__ import annotations

from typing import Protocol

from auditmind_api.ingestion.domain.entities import Chunk, Document, DocumentStatus


class BlobStorage(Protocol):
    async def put(
        self, *, engagement_id: str, document_id: str, filename: str, content: bytes
    ) -> str:
        """Stores raw bytes and returns a storage URI the ``Document`` row persists.

        This port is what makes swapping the local filesystem adapter for a real Azure Blob
        Storage adapter a change to one infrastructure file, never to application logic.
        """
        ...

    async def get(self, storage_uri: str) -> bytes: ...


class ParserRouter(Protocol):
    """What the application layer depends on: "parse this content, given its mime type."

    The per-format parser protocol (``can_parse`` / ``parse(content)``) that a concrete router
    composes several of is an infrastructure-internal detail (``infrastructure/parsers.py``) —
    the application layer never needs to know a router is composed of swappable per-format
    parsers at all, only that it can hand over bytes and a mime type and get text back.
    """

    def parse(self, *, mime_type: str, content: bytes) -> str:
        """Returns extracted plain text. Raises ``DocumentParsingError`` or
        ``UnsupportedMimeTypeError`` on failure."""
        ...


class DocumentRepository(Protocol):
    async def get_by_hash(self, *, engagement_id: str, sha256_hash: str) -> Document | None: ...

    async def create(self, document: Document) -> Document: ...

    async def update_status(self, *, document_id: str, status: DocumentStatus) -> None: ...

    async def get(self, document_id: str) -> Document | None: ...

    async def list_for_engagement(self, engagement_id: str) -> list[Document]:
        """Relies on the Row-Level Security context already bound on the session — no
        ``engagement_id`` filter is applied in the query itself beyond what the caller passes,
        matching the identity context's convention of trusting the database's own isolation
        guarantee wherever RLS covers the table."""
        ...


class ChunkRepository(Protocol):
    async def bulk_create(self, chunks: list[Chunk]) -> None: ...

    async def list_for_document(self, document_id: str) -> list[Chunk]: ...
