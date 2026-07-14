"""SQLAlchemy ORM models for the ``agent`` Postgres schema.

The only module in this service allowed to know about SQLAlchemy — the same "persistence model is
not a domain entity" separation ``apps/api`` establishes. Three tables: ``agent.runs``,
``agent.checkpoints``, and ``agent.hitl_interrupts``.

``agent.checkpoints`` is modelled here so this service's migration owns the table, but note who
writes it: the LangGraph Postgres checkpointer (``langgraph-checkpoint-postgres``) owns the *rows*
— it creates its own auxiliary tables and reads/writes the serialized state blobs itself. This ORM
model exists for schema ownership, grants, and RLS; application code never inserts into it directly
(that is the checkpointer's job, invoked implicitly by the compiled graph on every superstep).

**Columns referencing ``identity.*`` carry no ORM-level ``ForeignKey``, only a plain UUID column.**
The *database* constraint still exists — this service's own migration declares a real
``sa.ForeignKey(...)`` in its raw DDL (``migrations/versions/e1f2a3b4c5d6_...py``), enforced by
Postgres regardless of what the ORM knows. But this service's ``Base``/metadata (``orm_base.py``)
is deliberately its own, separate from ``apps/api``'s — the two are separate deployables that never
share a Python package — so it has no ``identity.users``/``identity.engagements`` ``Table`` object
registered on it at all. SQLAlchemy's unit-of-work needs every ``ForeignKey`` target actually
resolvable within the *same* ``MetaData`` to compute flush-order dependencies, so an ORM-level FK
string pointing at a table this metadata has never heard of raises `NoReferencedTableError` the
first time the ORM tries to insert a row — confirmed by hitting exactly that error against the real
stack before this comment was written. Cross-schema FKs at the DDL/migration layer (which never
need a `Table` object, only a string for `CREATE TABLE ... REFERENCES`) are unaffected and still
declared there — see that migration's own comment for the ``apps/api`` convention this still
follows at the SQL level, just not at the ORM level across a service boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_orchestrator.infrastructure.orm_base import Base


class AgentRunModel(Base):
    """``agent.runs`` — one row per LangGraph invocation."""

    __tablename__ = "runs"
    __table_args__ = {"schema": "agent"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    use_case: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    initiated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentCheckpointModel(Base):
    """``agent.checkpoints`` — the LangGraph checkpointer's durable state snapshots. ``state`` is
    JSONB: every state channel is JSON-serializable by construction, so the whole state persists
    verbatim per superstep. Written by the checkpointer, not by this service's application code."""

    __tablename__ = "checkpoints"
    __table_args__ = {"schema": "agent"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent.runs.id"), nullable=False, index=True
    )
    # Denormalized onto the checkpoint row so its RLS policy is a single-hop membership check, not a
    # join through agent.runs — the same denormalization convention every engagement-scoped table
    # in this platform uses.
    engagement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HitlInterruptModel(Base):
    """``agent.hitl_interrupts`` — one row per human checkpoint.

    Created ``pending`` (``decision`` NULL) when the graph pauses; resolved when a reviewer
    dispositions it. ``decision IS NULL`` distinguishes an open interrupt from a resolved one, the
    same append-then-resolve shape ``apps/api``'s reporting context uses for finding disposition —
    both the AI draft and the human decision stay durable.
    """

    __tablename__ = "hitl_interrupts"
    __table_args__ = {"schema": "agent"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent.runs.id"), nullable=False, index=True
    )
    engagement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageModel(Base):
    """``agent.messages`` — one turn in a user's private AI Copilot conversation for one
    engagement (the chat interface's persistence). Never updated once written; a conversation only
    grows."""

    __tablename__ = "messages"
    __table_args__ = {"schema": "agent"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    message_type: Mapped[str] = mapped_column(String, nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent.runs.id"), nullable=True
    )
    # Python attribute named `extra`, not `metadata` — `Base.metadata` is SQLAlchemy Declarative's
    # own reserved attribute (the MetaData object), so a column attribute of that name would shadow
    # it. The DB column itself is still named `metadata`, via the explicit column-name argument.
    extra: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
