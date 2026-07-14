"""FastAPI dependencies wiring the Audit Trail context into the request lifecycle."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.audit_trail.application.services import AuditTrailService
from auditmind_api.audit_trail.infrastructure.repository import PostgresAuditEventRepository
from auditmind_api.shared.database import get_db_session


def get_audit_trail_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditTrailService:
    return AuditTrailService(event_repository=PostgresAuditEventRepository(session))
