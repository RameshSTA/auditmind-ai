"""SQLAlchemy ORM models for the ``ingestion`` Postgres schema.

This is the only module in the ingestion context allowed to know about SQLAlchemy — the same
"persistence model is not a domain entity" separation the identity context already
established.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": "ingestion"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    source_connector: Mapped[str] = mapped_column(String, nullable=False, default="manual_upload")
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="received")
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion.documents.id"), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ingested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), nullable=False
    )


class ChunkModel(Base):
    __tablename__ = "chunks"
    __table_args__ = {"schema": "ingestion"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion.documents.id"), nullable=False, index=True
    )
    # Denormalized (also derivable via a join through documents) — kept directly on the row so
    # every RLS policy and query filter in this schema follows the same "filter/scope by
    # engagement_id in one hop" convention, without a join being required just to enforce
    # isolation.
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Generated, GIN-indexed column backing full-text search (the "keyword leg" of retrieval) —
    # computed by Postgres from `text` on every insert, never written by application code. Mapped
    # here purely for schema documentation/consistency; only the `retrieval` context's raw-SQL
    # adapter ever reads it (see that context's `infrastructure/search_index.py`), matching the
    # "no bounded context imports another's ORM model" convention even though `retrieval` reads a
    # column `ingestion` owns.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True), nullable=False
    )
