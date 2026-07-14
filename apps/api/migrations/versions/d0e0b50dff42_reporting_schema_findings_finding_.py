"""reporting schema: findings, finding_evidence, reports, report_findings

Implements the ``reporting.findings`` / ``reporting.finding_evidence`` / ``reporting.reports``
cluster from Phase 4 §1 (scoped to what Increment 04 actually builds — ``source_run_id`` is
deferred until ``agent.runs`` exists, see the increment doc), plus a ``report_findings`` junction
table that snapshots exactly which confirmed findings a report compiled, not designed in the
Phase 4 doc's abbreviated column list but necessary for a report to reference its own contents.

Reuses Increment 03's stronger, subquery-based Row-Level Security pattern (derived fresh from
``identity.engagement_members`` on every query) across all four tables — every one of them has a
genuine write path this increment exercises.

Revision ID: d0e0b50dff42
Revises: ea15243395ac
Create Date: 2026-07-10 15:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "d0e0b50dff42"
down_revision: str | None = "ea15243395ac"
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
    op.execute("CREATE SCHEMA IF NOT EXISTS reporting")

    op.create_table(
        "findings",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        sa.Column("control_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        _uuid_fk_column("created_by", references="identity.users.id"),
        _timestamp_column("created_at"),
        _uuid_fk_column("reviewed_by", references="identity.users.id", nullable=True),
        _timestamp_column("reviewed_at", nullable=True),
        sa.Column("disposition_reason", sa.Text(), nullable=True),
        schema="reporting",
    )
    op.create_index("ix_findings_engagement_id", "findings", ["engagement_id"], schema="reporting")

    op.create_table(
        "finding_evidence",
        _uuid_pk_column(),
        _uuid_fk_column("finding_id", references="reporting.findings.id"),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        _uuid_fk_column("chunk_id", references="ingestion.chunks.id"),
        sa.Column("citation_text", sa.Text(), nullable=False),
        _timestamp_column("created_at"),
        schema="reporting",
    )
    op.create_index(
        "ix_finding_evidence_finding_id", "finding_evidence", ["finding_id"], schema="reporting"
    )
    op.create_index(
        "ix_finding_evidence_engagement_id",
        "finding_evidence",
        ["engagement_id"],
        schema="reporting",
    )

    op.create_table(
        "reports",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        sa.Column("version", sa.Integer(), nullable=False),
        _uuid_fk_column("generated_by", references="identity.users.id"),
        _timestamp_column("generated_at"),
        sa.Column("exported_uri", sa.String(), nullable=True),
        schema="reporting",
    )
    op.create_index("ix_reports_engagement_id", "reports", ["engagement_id"], schema="reporting")
    # A given engagement never has two reports at the same version — the safety net behind
    # `ReportRepository.next_version_for_engagement`'s COUNT-based computation, the same "trust the
    # constraint, not just the application check" discipline as documents' sha256 uniqueness.
    op.create_unique_constraint(
        "uq_reports_engagement_version", "reports", ["engagement_id", "version"], schema="reporting"
    )

    op.create_table(
        "report_findings",
        _uuid_fk_column("report_id", references="reporting.reports.id"),
        _uuid_fk_column("finding_id", references="reporting.findings.id"),
        sa.PrimaryKeyConstraint("report_id", "finding_id"),
        schema="reporting",
    )

    # Least-privilege grants (Phase 11 §7/§8). `findings` is the one table this context actually
    # updates post-insert (the confirm/reject disposition) — scoped to exactly those four columns,
    # never a blanket UPDATE, the same discipline `ingestion.documents`' status-only grant set.
    op.execute(f"GRANT USAGE ON SCHEMA reporting TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON reporting.findings TO {_APP_ROLE}")
    op.execute(
        f"GRANT UPDATE (status, reviewed_by, reviewed_at, disposition_reason) "
        f"ON reporting.findings TO {_APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON reporting.finding_evidence TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON reporting.reports TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON reporting.report_findings TO {_APP_ROLE}")

    for table in ("findings", "finding_evidence", "reports", "report_findings"):
        op.execute(f"ALTER TABLE reporting.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE reporting.{table} FORCE ROW LEVEL SECURITY")

    for table in ("findings", "finding_evidence", "reports"):
        op.execute(
            f"""
            CREATE POLICY {table}_engagement_member_only ON reporting.{table}
            USING ({_MEMBERSHIP_CHECK.format(table=table)})
            """
        )

    # `report_findings` has no `engagement_id` column of its own (Phase 4 §1's schema doesn't list
    # one for this junction table) — its policy joins through `reporting.reports` instead, the same
    # "is the caller a member of the engagement this row ultimately belongs to" question, answered
    # via the one extra hop this table's shape requires.
    op.execute(
        """
        CREATE POLICY report_findings_engagement_member_only ON reporting.report_findings
        USING (
            EXISTS (
                SELECT 1 FROM reporting.reports r
                JOIN identity.engagement_members em ON em.engagement_id = r.engagement_id
                WHERE r.id = report_findings.report_id
                  AND em.user_id = current_setting('app.current_user_id', true)::uuid
            )
        )
        """
    )


def downgrade() -> None:
    for table in ("report_findings", "reports", "finding_evidence", "findings"):
        op.execute(f"DROP POLICY IF EXISTS {table}_engagement_member_only ON reporting.{table}")
    op.drop_table("report_findings", schema="reporting")
    op.drop_table("reports", schema="reporting")
    op.drop_table("finding_evidence", schema="reporting")
    op.drop_table("findings", schema="reporting")
