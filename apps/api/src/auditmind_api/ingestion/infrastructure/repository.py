"""Postgres-backed implementations of the ingestion repository ports."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.ingestion.domain.entities import Chunk, Document, DocumentStatus
from auditmind_api.ingestion.infrastructure.models import ChunkModel, DocumentModel


def _to_document_entity(model: DocumentModel) -> Document:
    return Document(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        source_connector=model.source_connector,
        original_filename=model.original_filename,
        storage_uri=model.storage_uri,
        sha256_hash=model.sha256_hash,
        mime_type=model.mime_type,
        status=DocumentStatus(model.status),
        ingested_at=model.ingested_at,
        ingested_by=str(model.ingested_by),
        duplicate_of=str(model.duplicate_of) if model.duplicate_of else None,
    )


def _to_chunk_entity(model: ChunkModel) -> Chunk:
    return Chunk(
        id=str(model.id),
        document_id=str(model.document_id),
        engagement_id=str(model.engagement_id),
        chunk_index=model.chunk_index,
        text=model.text,
        char_start=model.char_start,
        char_end=model.char_end,
        created_at=model.created_at,
    )


class PostgresDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash(self, *, engagement_id: str, sha256_hash: str) -> Document | None:
        result = await self._session.execute(
            select(DocumentModel).where(
                DocumentModel.engagement_id == engagement_id,
                DocumentModel.sha256_hash == sha256_hash,
            )
        )
        model = result.scalars().first()
        return _to_document_entity(model) if model else None

    async def create(self, document: Document) -> Document:
        model = DocumentModel(
            id=document.id,
            engagement_id=document.engagement_id,
            source_connector=document.source_connector,
            original_filename=document.original_filename,
            storage_uri=document.storage_uri,
            sha256_hash=document.sha256_hash,
            mime_type=document.mime_type,
            status=document.status.value,
            ingested_by=document.ingested_by,
            duplicate_of=document.duplicate_of,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_document_entity(model)

    async def update_status(self, *, document_id: str, status: DocumentStatus) -> None:
        result = await self._session.execute(
            select(DocumentModel).where(DocumentModel.id == document_id)
        )
        model = result.scalar_one()
        model.status = status.value
        await self._session.flush()

    async def get(self, document_id: str) -> Document | None:
        result = await self._session.execute(
            select(DocumentModel).where(DocumentModel.id == document_id)
        )
        model = result.scalars().first()
        return _to_document_entity(model) if model else None

    async def list_for_engagement(self, engagement_id: str) -> list[Document]:
        result = await self._session.execute(
            select(DocumentModel).where(DocumentModel.engagement_id == engagement_id)
        )
        return [_to_document_entity(m) for m in result.scalars().all()]


class PostgresChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create(self, chunks: list[Chunk]) -> None:
        models = [
            ChunkModel(
                id=c.id,
                document_id=c.document_id,
                engagement_id=c.engagement_id,
                chunk_index=c.chunk_index,
                text=c.text,
                char_start=c.char_start,
                char_end=c.char_end,
            )
            for c in chunks
        ]
        self._session.add_all(models)
        await self._session.flush()

    async def list_for_document(self, document_id: str) -> list[Chunk]:
        result = await self._session.execute(
            select(ChunkModel)
            .where(ChunkModel.document_id == document_id)
            .order_by(ChunkModel.chunk_index)
        )
        return [_to_chunk_entity(m) for m in result.scalars().all()]
