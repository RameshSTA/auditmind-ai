"""SQLAlchemy ORM models for the ``identity`` Postgres schema (Phase 4 §1).

This is the only module in the identity context allowed to know about SQLAlchemy. Domain entities
(plain dataclasses in ``domain.entities``) are mapped to/from these models at the repository
boundary and are never used interchangeably with them — the same "persistence model is not a
domain entity" separation Phase 3 §4 already applies to API request/response schemas, applied
here to the ORM layer instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from auditmind_api.shared.orm_base import Base


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entra_object_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantModel(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EngagementModel(Base):
    __tablename__ = "engagements"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    business_unit: Mapped[str] = mapped_column(String, nullable=False, default="")
    sensitivity_tier: Mapped[str] = mapped_column(String, nullable=False, default="internal")
    status: Mapped[str] = mapped_column(String, nullable=False, default="planning")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CredentialModel(Base):
    """Local email/password credentials (Increment 14: self-service signup). One row per user,
    enforced by ``user_id`` being both primary key and a unique FK — a second ``create`` call for
    the same user is a programming error, not a supported "update password" path (unbuilt)."""

    __tablename__ = "credentials"
    __table_args__ = {"schema": "identity"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EngagementMembershipModel(Base):
    """Row-Level Security is enabled on this table by the Alembic migration, not here — SQLAlchemy
    models describe shape, not policy (Phase 4 §12's policy lives in the migration, Increment 02
    docs)."""

    __tablename__ = "engagement_members"
    __table_args__ = {"schema": "identity"}

    engagement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.engagements.id"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity.users.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
