"""audit_trail schema: events (append-only)

Implements ``audit_trail.events`` — the platform's evidentiary record of who (or what) did what,
when, and what changed. Every other schema's write paths get *some* durability from their own row
history (a finding's `status` column shows its current disposition), but none of them retain what
the row looked like *before* the last change, or who made it, once a second change overwrites the
first. This table exists to make that reconstructible, permanently.

The defining property — no UPDATE or DELETE grant exists on this table for any application role;
a correction is a new row referencing the original event's id, the table only ever grows — is
enforced here the same way every other least-privilege guarantee in this codebase is enforced: by
what is (and is not) granted to `auditmind_app`, not by application code choosing not to call an
UPDATE it technically could. `AuditEventRepository` (the application-layer port) also has no
`update`/`delete` method at all — belt and suspenders, not either/or.

Revision ID: a7c4e912f568
Revises: f3a1c9d7b204
Create Date: 2026-07-10 17:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "a7c4e912f568"
down_revision: str | None = "f3a1c9d7b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

_MEMBERSHIP_CHECK = """
    EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = events.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS audit_trail")

    op.create_table(
        "events",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.engagements.id"),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(), nullable=False),
        # Polymorphic — no FK, same convention as `risk.risk_scores.subject_id`.
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=True),
        sa.Column("before_state", JSONB(), nullable=True),
        sa.Column("after_state", JSONB(), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="audit_trail",
    )
    op.create_index(
        "ix_audit_trail_events_engagement_id", "events", ["engagement_id"], schema="audit_trail"
    )

    # The whole point of this table: SELECT and INSERT only. No UPDATE, no DELETE — not even a
    # column-scoped one, unlike every other table's disposition-column grant in this codebase.
    # There is no column on this table that is *ever* meant to change after insert.
    op.execute(f"GRANT USAGE ON SCHEMA audit_trail TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_trail.events TO {_APP_ROLE}")

    op.execute("ALTER TABLE audit_trail.events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_trail.events FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY events_engagement_member_only ON audit_trail.events
        USING ({_MEMBERSHIP_CHECK})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS events_engagement_member_only ON audit_trail.events")
    op.drop_table("events", schema="audit_trail")
