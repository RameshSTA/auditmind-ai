"""FastAPI dependencies wiring the Ingestion context into the request lifecycle."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.ingestion.application.services import IngestionService
from auditmind_api.ingestion.infrastructure.local_blob_storage import LocalFilesystemBlobStorage
from auditmind_api.ingestion.infrastructure.parsers import default_parser_router
from auditmind_api.ingestion.infrastructure.repository import (
    PostgresChunkRepository,
    PostgresDocumentRepository,
)
from auditmind_api.shared.database import get_db_session
from auditmind_api.shared.settings import Settings, get_settings


def get_ingestion_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> IngestionService:
    return IngestionService(
        document_repository=PostgresDocumentRepository(session),
        chunk_repository=PostgresChunkRepository(session),
        blob_storage=LocalFilesystemBlobStorage(Path(settings.blob_storage_root)),
        parser_router=default_parser_router(),
    )
