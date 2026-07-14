"""risk schema: transactions, anomalies

Implements the ``risk.transactions`` / ``risk.anomalies`` pair, scoped to the rule engine's three
checks built here (Benford's Law, duplicate-payment matching, threshold/round-dollar detection).
``risk.risk_scores`` (the weighted-logistic combiner's output) is deferred entirely: it has
nothing to combine until the ML-dependent signals (Isolation Forest, HDBSCAN, graph centrality)
not built here also exist.

Reuses the subquery-based Row-Level Security pattern established elsewhere on both tables — both
have a genuine write path exercised here.

Revision ID: f3a1c9d7b204
Revises: d0e0b50dff42
Create Date: 2026-07-10 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "f3a1c9d7b204"
down_revision: str | None = "d0e0b50dff42"
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
    op.execute("CREATE SCHEMA IF NOT EXISTS risk")

    op.create_table(
        "transactions",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        sa.Column("source_system", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        _uuid_fk_column("created_by", references="identity.users.id"),
        _timestamp_column("created_at"),
        schema="risk",
    )
    op.create_index(
        "ix_transactions_engagement_id", "transactions", ["engagement_id"], schema="risk"
    )

    op.create_table(
        "anomalies",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        # Nullable: a Benford's Law deviation is a population-level verdict, not tied to one
        # transaction (see `risk/domain/entities.py`'s `Anomaly.transaction_id` docstring).
        _uuid_fk_column("transaction_id", references="risk.transactions.id", nullable=True),
        sa.Column("anomaly_type", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _timestamp_column("detected_at"),
        _uuid_fk_column("reviewed_by", references="identity.users.id", nullable=True),
        _timestamp_column("reviewed_at", nullable=True),
        schema="risk",
    )
    op.create_index("ix_anomalies_engagement_id", "anomalies", ["engagement_id"], schema="risk")

    # Least-privilege grants. `transactions` is insert-only from the app role's perspective —
    # nothing here ever mutates an imported transaction. `anomalies` gets a column-scoped UPDATE
    # for exactly the disposition transition, the same pattern `reporting.findings` established.
    op.execute(f"GRANT USAGE ON SCHEMA risk TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON risk.transactions TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON risk.anomalies TO {_APP_ROLE}")
    op.execute(
        f"GRANT UPDATE (status, reviewed_by, reviewed_at) ON risk.anomalies TO {_APP_ROLE}"
    )

    for table in ("transactions", "anomalies"):
        op.execute(f"ALTER TABLE risk.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE risk.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_engagement_member_only ON risk.{table}
            USING ({_MEMBERSHIP_CHECK.format(table=table)})
            """
        )


def downgrade() -> None:
    for table in ("anomalies", "transactions"):
        op.execute(f"DROP POLICY IF EXISTS {table}_engagement_member_only ON risk.{table}")
    op.drop_table("anomalies", schema="risk")
    op.drop_table("transactions", schema="risk")
