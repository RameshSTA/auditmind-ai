"""SQLAlchemy ORM models for the ``kg`` Postgres schema (Phase 4 §4) — the Postgres side of the
Knowledge Graph context's "bridge" to Neo4j. Registered on the shared ``Base``
(``shared/orm_base.py``) so Alembic autogenerate sees them, the same convention every other
context's ``infrastructure/models.py`` follows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base


class EntityCandidateModel(Base):
    __tablename__ = "entity_candidates"
    __table_args__ = {"schema": "kg"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    source_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risk.transactions.id"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    raw_name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="resolved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityResolutionMapModel(Base):
    __tablename__ = "entity_resolution_map"
    __table_args__ = {"schema": "kg"}

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kg.entity_candidates.id"), primary_key=True
    )
    neo4j_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("1.0")
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=True
    )
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
