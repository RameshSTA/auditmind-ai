"""ingestion schema: documents, chunks, engagement-membership RLS

Implements the ``ingestion`` schema (scoped to what is built here — ``document_versions`` is
deferred) with a stronger Row-Level Security pattern than the identity schema's self-only policy:
rather than a session variable the application must remember to set correctly, these policies
derive authorization directly from ``identity.engagement_members`` via a subquery — a user can
read or write a row here if and only if they are *currently* a member of its ``engagement_id``,
checked fresh against the actual membership table on every query.

Revision ID: ea15243395ac
Revises: a0bf8e859800
Create Date: 2026-07-10 14:29:33.613311

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "ea15243395ac"
down_revision: str | None = "a0bf8e859800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

# Shared by both tables' policies — a user may act on a row if and only if they are presently a
# member of that row's engagement, checked against the actual membership table, not a cached
# session variable naming an engagement.
_MEMBERSHIP_CHECK = """
    EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = {table}.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""


def _uuid_pk_column() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _uuid_fk_column(name: str, *, references: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, UUID(as_uuid=True), sa.ForeignKey(references), nullable=nullable)


def _timestamp_column(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ingestion")

    op.create_table(
        "documents",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        sa.Column("source_connector", sa.String(), nullable=False, server_default="manual_upload"),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        _uuid_fk_column("duplicate_of", references="ingestion.documents.id", nullable=True),
        _timestamp_column("ingested_at"),
        _uuid_fk_column("ingested_by", references="identity.users.id"),
        schema="ingestion",
    )
    op.create_index(
        "ix_documents_engagement_id", "documents", ["engagement_id"], schema="ingestion"
    )
    # Duplicate detection is a database constraint, not just an application-code
    # check-then-insert — the latter has a race condition between two concurrent uploads of the
    # same file that a unique constraint closes entirely.
    op.create_unique_constraint(
        "uq_documents_engagement_sha256",
        "documents",
        ["engagement_id", "sha256_hash"],
        schema="ingestion",
    )

    op.create_table(
        "chunks",
        _uuid_pk_column(),
        _uuid_fk_column("document_id", references="ingestion.documents.id"),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        _timestamp_column("created_at"),
        schema="ingestion",
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"], schema="ingestion")
    op.create_index("ix_chunks_engagement_id", "chunks", ["engagement_id"], schema="ingestion")

    # Least-privilege grants. Unlike identity.engagement_members (read-only for the app role),
    # this context's whole purpose is writing new documents and chunks, so INSERT is genuinely
    # needed — but UPDATE is scoped to the one column the app actually mutates post-insert
    # (`status`), not a blanket UPDATE grant.
    op.execute(f"GRANT USAGE ON SCHEMA ingestion TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON ingestion.documents TO {_APP_ROLE}")
    op.execute(f"GRANT UPDATE (status) ON ingestion.documents TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON ingestion.chunks TO {_APP_ROLE}")

    for table in ("documents", "chunks"):
        op.execute(f"ALTER TABLE ingestion.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE ingestion.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_engagement_member_only ON ingestion.{table}
            USING ({_MEMBERSHIP_CHECK.format(table=table)})
            """
        )


def downgrade() -> None:
    for table in ("chunks", "documents"):
        op.execute(f"DROP POLICY IF EXISTS {table}_engagement_member_only ON ingestion.{table}")
    op.drop_table("chunks", schema="ingestion")
    op.drop_table("documents", schema="ingestion")
