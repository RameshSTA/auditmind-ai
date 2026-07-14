"""FastAPI dependencies wiring the Investigations context into the request lifecycle."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.investigations.application.services import InvestigationService
from auditmind_api.investigations.infrastructure.audit_recorder import PostgresAuditTrailRecorder
from auditmind_api.investigations.infrastructure.repository import (
    PostgresInvestigationItemRepository,
    PostgresInvestigationRepository,
)
from auditmind_api.investigations.infrastructure.subject_lookup import (
    PostgresInvestigationSubjectLookup,
)
from auditmind_api.shared.database import get_db_session


def get_investigation_service(
    session: AsyncSession = Depends(get_db_session),
) -> InvestigationService:
    return InvestigationService(
        investigation_repository=PostgresInvestigationRepository(session),
        item_repository=PostgresInvestigationItemRepository(session),
        subject_lookup=PostgresInvestigationSubjectLookup(session),
        audit_recorder=PostgresAuditTrailRecorder(session),
    )
