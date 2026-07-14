"""SQLAlchemy ORM models for the ``reporting`` Postgres schema (Phase 4 §1).

This is the only module in the reporting context allowed to know about SQLAlchemy — the same
"persistence model is not a domain entity" separation ``identity`` and ``ingestion`` already
established. Cross-context foreign keys (``identity.*``, ``ingestion.chunks``) are declared as
table-path strings, never by importing another context's ORM class (Increment 03's convention).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base


class FindingModel(Base):
    __tablename__ = "findings"
    __table_args__ = {"schema": "reporting"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    control_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disposition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class FindingEvidenceModel(Base):
    __tablename__ = "finding_evidence"
    __table_args__ = {"schema": "reporting"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reporting.findings.id"), nullable=False, index=True
    )
    # Denormalized, same convention as ingestion.chunks.engagement_id — keeps every RLS policy in
    # this schema a single-hop check rather than a join through `findings` just to enforce
    # isolation.
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion.chunks.id"), nullable=False
    )
    citation_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportModel(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "reporting"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # The rendered, evidence-cited Markdown document — snapshotted once at generation time, same
    # as `finding_ids` below, so a report's content never drifts if a finding's evidence changes
    # after the fact.
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    # Left null in this increment — actual document export (PDF/docx generation) is deferred, see
    # the increment doc's "what's explicitly deferred" section.
    exported_uri: Mapped[str | None] = mapped_column(String, nullable=True)


class ReportFindingModel(Base):
    """Junction table: which confirmed findings a given report snapshotted at generation time."""

    __tablename__ = "report_findings"
    __table_args__ = {"schema": "reporting"}

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reporting.reports.id"), primary_key=True
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reporting.findings.id"), primary_key=True
    )
