"""SQLAlchemy ORM models for the ``investigations`` Postgres schema.

This is the only module in the investigations context allowed to know about SQLAlchemy — the same
"persistence model is not a domain entity" separation every prior context established.
Cross-context foreign keys (``identity.*``) are declared as table-path strings, never by importing
another context's ORM class. ``item``'s ``subject_id`` is deliberately not a foreign key — it's a
polymorphic reference to whichever table ``subject_type`` names, the same shape
``risk.risk_scores.subject_id`` already established.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base


class InvestigationModel(Base):
    __tablename__ = "investigations"
    __table_args__ = {"schema": "investigations"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)


class InvestigationItemModel(Base):
    __tablename__ = "investigation_items"
    __table_args__ = {"schema": "investigations"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("investigations.investigations.id"),
        nullable=False,
        index=True,
    )
    # Denormalized, same convention as reporting.finding_evidence.engagement_id — keeps this
    # table's RLS policy a single-hop check rather than a join through `investigations`.
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
