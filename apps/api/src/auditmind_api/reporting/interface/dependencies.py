"""FastAPI dependencies wiring the Reporting context into the request lifecycle."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.reporting.application.services import ReportingService
from auditmind_api.reporting.infrastructure.audit_recorder import PostgresAuditTrailRecorder
from auditmind_api.reporting.infrastructure.chunk_lookup import PostgresChunkLookup
from auditmind_api.reporting.infrastructure.local_report_storage import (
    LocalFilesystemReportStorage,
)
from auditmind_api.reporting.infrastructure.repository import (
    PostgresFindingEvidenceRepository,
    PostgresFindingRepository,
    PostgresReportRepository,
)
from auditmind_api.shared.database import get_db_session
from auditmind_api.shared.settings import Settings, get_settings


def get_reporting_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ReportingService:
    return ReportingService(
        finding_repository=PostgresFindingRepository(session),
        evidence_repository=PostgresFindingEvidenceRepository(session),
        report_repository=PostgresReportRepository(session),
        chunk_lookup=PostgresChunkLookup(session),
        audit_recorder=PostgresAuditTrailRecorder(session),
        # A subdirectory of the same blob storage root ingestion already uses (Increment 03) —
        # a distinct prefix, not a distinct setting, since there's nothing engagement-specific
        # about "where exported reports live" that ingestion's documents don't already need too.
        report_file_storage=LocalFilesystemReportStorage(
            Path(settings.blob_storage_root) / "reports"
        ),
    )
