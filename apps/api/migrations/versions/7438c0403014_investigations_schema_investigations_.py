"""investigations schema: investigations, investigation_items

The Investigations context turns findings/anomalies/transactions an investigator has already
authored or reviewed elsewhere into a working case: an open/closed container with a title,
description, and a set of linked items, closing with a documented conclusion. No new upstream
data — every item this schema can point at (a finding, an anomaly, a transaction) already exists
in ``reporting``/``risk``; ``investigation_items.subject_type``/``subject_id`` is the same
polymorphic reference shape ``risk.risk_scores`` established, deliberately not a foreign key since
the table it names varies by row.

Reuses the subquery-based Row-Level Security pattern every prior context's schema establishes —
both tables get their own ``engagement_id`` (denormalized on ``investigation_items``, the same
"single-hop policy" reasoning ``reporting.finding_evidence`` documents), so every policy here is a
single membership-subquery check, no cross-table join required.

Revision ID: 7438c0403014
Revises: c8f21a3d5e9b
Create Date: 2026-07-13 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "7438c0403014"
down_revision: str | None = "c8f21a3d5e9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

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


def _timestamp_column(name: str, *, nullable: bool = False) -> sa.Column:
    server_default = None if nullable else sa.func.now()
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=server_default, nullable=nullable
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS investigations")

    op.create_table(
        "investigations",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        _uuid_fk_column("created_by", references="identity.users.id"),
        _timestamp_column("created_at"),
        _uuid_fk_column("closed_by", references="identity.users.id", nullable=True),
        _timestamp_column("closed_at", nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        schema="investigations",
    )
    op.create_index(
        "ix_investigations_engagement_id",
        "investigations",
        ["engagement_id"],
        schema="investigations",
    )

    op.create_table(
        "investigation_items",
        _uuid_pk_column(),
        _uuid_fk_column("investigation_id", references="investigations.investigations.id"),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        # Not a foreign key — polymorphic reference to whichever table `subject_type` names, the
        # same "no models registry yet" reasoning `risk.risk_scores.subject_id` documents.
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        _uuid_fk_column("added_by", references="identity.users.id"),
        _timestamp_column("added_at"),
        sa.Column("note", sa.Text(), nullable=True),
        schema="investigations",
    )
    op.create_index(
        "ix_investigation_items_investigation_id",
        "investigation_items",
        ["investigation_id"],
        schema="investigations",
    )
    op.create_index(
        "ix_investigation_items_engagement_id",
        "investigation_items",
        ["engagement_id"],
        schema="investigations",
    )
    # The same item (same subject) can't be added to the same investigation twice — a duplicate
    # add is a client bug (a stale UI missing the item already in the working set), not a state
    # the API silently accepts a second row for.
    op.create_unique_constraint(
        "uq_investigation_items_investigation_subject",
        "investigation_items",
        ["investigation_id", "subject_type", "subject_id"],
        schema="investigations",
    )

    # Least-privilege grants. `investigations` is the one table this context updates post-insert
    # (the close disposition) — scoped to exactly those columns, never a blanket UPDATE, the same
    # discipline `reporting.findings`'s disposition-column grant uses.
    op.execute(f"GRANT USAGE ON SCHEMA investigations TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON investigations.investigations TO {_APP_ROLE}")
    op.execute(
        f"GRANT UPDATE (status, closed_by, closed_at, conclusion) "
        f"ON investigations.investigations TO {_APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON investigations.investigation_items TO {_APP_ROLE}")

    for table in ("investigations", "investigation_items"):
        op.execute(f"ALTER TABLE investigations.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE investigations.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_engagement_member_only ON investigations.{table}
            USING ({_MEMBERSHIP_CHECK.format(table=table)})
            """
        )


def downgrade() -> None:
    for table in ("investigation_items", "investigations"):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_engagement_member_only ON investigations.{table}"
        )
    op.drop_table("investigation_items", schema="investigations")
    op.drop_table("investigations", schema="investigations")
