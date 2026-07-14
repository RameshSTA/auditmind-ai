"""SQLAlchemy ORM model for the ``retrieval`` Postgres schema.

``chunk_embeddings`` is the first table this context owns — before it existed, this context only
ever read a column ``ingestion.chunks`` owns (``search_vector``). Registered on the shared
``Base`` (``shared/orm_base.py``) so Alembic autogenerate sees it, the same convention every other
context's ``infrastructure/models.py`` follows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base

# BGE-M3's dense output dimension — fixed by the model, matches the migration.
_EMBEDDING_DIM = 1024


class ChunkEmbeddingModel(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = {"schema": "retrieval"}

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion.chunks.id"), primary_key=True
    )
    model_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Denormalized (also derivable via a join through ingestion.chunks) — the same "filter/scope
    # by engagement_id in one hop" convention every RLS-backed table in this codebase follows
    # (see ingestion.chunks' own model for the identical rationale).
    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), nullable=False, index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
