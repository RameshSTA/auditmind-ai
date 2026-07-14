"""Application service orchestrating document ingestion.

No SQL, no HTTP, no filesystem-specific code here — only coordination of the ports, which is
what makes this layer testable against fakes, the same pattern
``identity.application.services.IdentityService`` established.
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from datetime import UTC, datetime

from auditmind_api.ingestion.application.chunking import chunk_text
from auditmind_api.ingestion.domain.entities import Chunk, Document, DocumentStatus
from auditmind_api.ingestion.domain.ports import (
    BlobStorage,
    ChunkRepository,
    DocumentRepository,
    ParserRouter,
)


class IngestionService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        chunk_repository: ChunkRepository,
        blob_storage: BlobStorage,
        parser_router: ParserRouter,
    ) -> None:
        self._documents = document_repository
        self._chunks = chunk_repository
        self._storage = blob_storage
        self._parser = parser_router

    async def ingest_document(
        self,
        *,
        engagement_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
        ingested_by: str,
    ) -> Document:
        """Ingests one uploaded document: dedup check, store, parse, chunk, persist.

        An exact duplicate (same engagement, same content hash) never re-triggers parsing,
        chunking, or storage — caught at the cheapest possible point, before any of the
        comparatively expensive downstream work. The caller receives the original document back,
        not an error, since re-uploading the same file twice is a normal user action, not a
        mistake to reject.
        """
        sha256_hash = hashlib.sha256(content).hexdigest()

        existing = await self._documents.get_by_hash(
            engagement_id=engagement_id, sha256_hash=sha256_hash
        )
        if existing is not None:
            return existing

        document_id = str(uuid.uuid4())
        storage_uri = await self._storage.put(
            engagement_id=engagement_id,
            document_id=document_id,
            filename=filename,
            content=content,
        )

        document = Document(
            id=document_id,
            engagement_id=engagement_id,
            source_connector="manual_upload",
            original_filename=filename,
            storage_uri=storage_uri,
            sha256_hash=sha256_hash,
            mime_type=mime_type,
            status=DocumentStatus.RECEIVED,
            ingested_at=datetime.now(UTC),
            ingested_by=ingested_by,
        )
        document = await self._documents.create(document)

        # A parsing failure (unsupported type, malformed/scanned PDF) is recorded on the document
        # itself (status=failed) rather than raised past this point — the upload has already
        # succeeded and been stored; the caller should see a document in a "failed" state to
        # retry or investigate, not a 500 for a problem in one file among possibly many uploaded
        # in the same session.
        try:
            extracted_text = self._parser.parse(mime_type=mime_type, content=content)
            text_chunks = chunk_text(extracted_text)
            chunks = [
                Chunk(
                    id=str(uuid.uuid4()),
                    document_id=document.id,
                    engagement_id=engagement_id,
                    chunk_index=index,
                    text=text_chunk.text,
                    char_start=text_chunk.char_start,
                    char_end=text_chunk.char_end,
                    created_at=datetime.now(UTC),
                )
                for index, text_chunk in enumerate(text_chunks)
            ]
            await self._chunks.bulk_create(chunks)
            await self._documents.update_status(
                document_id=document.id, status=DocumentStatus.PARSED
            )
            document = dataclasses.replace(document, status=DocumentStatus.PARSED)
        except Exception:
            await self._documents.update_status(
                document_id=document.id, status=DocumentStatus.FAILED
            )
            raise

        return document

    async def list_documents(self, engagement_id: str) -> list[Document]:
        return await self._documents.list_for_engagement(engagement_id)

    async def list_chunks(self, document_id: str) -> list[Chunk]:
        return await self._chunks.list_for_document(document_id)
