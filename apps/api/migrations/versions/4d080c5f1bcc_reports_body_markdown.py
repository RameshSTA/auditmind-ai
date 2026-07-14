"""reporting.reports: add body_markdown

Adds the rendered, evidence-cited Markdown document a report snapshots at generation time —
until now ``Report`` was a bare tuple of finding ids with no rendered content, so the frontend
had nothing to show beyond a list of ids.

``body_markdown`` is backfilled empty for any pre-existing rows (the column can't be added
``NOT NULL`` without a default) and is always populated going forward by
``ReportingService.generate_report``.

Revision ID: 4d080c5f1bcc
Revises: d4a19e6b7f31
Create Date: 2026-07-13 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d080c5f1bcc"
down_revision: str | None = "d4a19e6b7f31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("body_markdown", sa.Text(), nullable=False, server_default=""),
        schema="reporting",
    )
    op.alter_column("reports", "body_markdown", server_default=None, schema="reporting")


def downgrade() -> None:
    op.drop_column("reports", "body_markdown", schema="reporting")
