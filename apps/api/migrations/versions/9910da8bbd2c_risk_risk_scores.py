"""risk.risk_scores

The weighted-linear risk combiner's output table, fed by four signal sources: rule engine,
Isolation Forest, HDBSCAN cohorts, and graph centrality.

``subject_type``/``subject_id`` is polymorphic (transaction / vendor / control) — currently only
``subject_type = 'transaction'`` is written, but the column shape doesn't need a migration to add
a second subject type later.

Reuses the subquery-based Row-Level Security pattern used throughout this codebase — `risk_scores`
has its own `engagement_id`, the same shape every other write-path table has.

Revision ID: 9910da8bbd2c
Revises: ae83cd542096
Create Date: 2026-07-11 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "9910da8bbd2c"
down_revision: str | None = "ae83cd542096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

_MEMBERSHIP_CHECK = """
    EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = risk_scores.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""


def upgrade() -> None:
    op.create_table(
        "risk_scores",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.engagements.id"),
            nullable=False,
        ),
        # Not a foreign key — 'transaction' is a polymorphic reference to whichever table
        # `subject_type` names, the same "no models registry yet" reasoning
        # `kg.entity_candidates.entity_type` uses for the identical shape.
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("score_version", sa.String(), nullable=False),
        sa.Column(
            "contributing_factors",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="risk",
    )
    op.create_index("ix_risk_scores_engagement_id", "risk_scores", ["engagement_id"], schema="risk")
    op.create_index(
        "ix_risk_scores_subject", "risk_scores", ["subject_type", "subject_id"], schema="risk"
    )
    # Recomputing a score for the same subject under the same model version refreshes the existing
    # row rather than accumulating stale duplicates — a transaction's score can legitimately change
    # between runs as the surrounding population changes (Isolation Forest and HDBSCAN are both
    # population-relative). A *different* `score_version` is a new row on purpose: it lets old
    # scores stay interpretable after the model changes.
    op.create_unique_constraint(
        "uq_risk_scores_subject_version",
        "risk_scores",
        ["subject_type", "subject_id", "score_version"],
        schema="risk",
    )

    # Least-privilege grants. UPDATE is scoped to exactly the columns a recompute refreshes —
    # never `engagement_id`, `subject_type`, or `subject_id` — the same "column-scoped UPDATE,
    # not a blanket grant" discipline `ingestion.documents.status` and `reporting.findings`'s
    # disposition columns use.
    op.execute(f"GRANT SELECT, INSERT ON risk.risk_scores TO {_APP_ROLE}")
    op.execute(
        f"GRANT UPDATE (score, contributing_factors, computed_at) ON risk.risk_scores "
        f"TO {_APP_ROLE}"
    )

    op.execute("ALTER TABLE risk.risk_scores ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk.risk_scores FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY risk_scores_engagement_member_only ON risk.risk_scores
        USING ({_MEMBERSHIP_CHECK})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS risk_scores_engagement_member_only ON risk.risk_scores")
    op.drop_table("risk_scores", schema="risk")
