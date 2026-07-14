"""SQLAlchemy ORM model for the ``audit_trail`` Postgres schema.

This is the only module in the audit_trail context allowed to know about SQLAlchemy. Note what's
*not* here: no ``onupdate``, no cascade-delete relationship, nothing that would let the ORM layer
mutate a row after insert — the migration that creates this table's grants is the actual
enforcement point (see its docstring), but the model itself is written to never suggest an update
path exists either.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base


class AuditEventModel(Base):
    __tablename__ = "events"
    __table_args__ = {"schema": "audit_trail"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    # Polymorphic — no FK; meaning is given by `actor_type`, not a fixed target table.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    before_state: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
