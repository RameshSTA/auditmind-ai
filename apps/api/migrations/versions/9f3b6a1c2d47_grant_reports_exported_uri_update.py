"""reporting.reports: grant UPDATE(exported_uri) to the app role

``reporting.reports`` was originally granted only ``SELECT, INSERT`` for the app role (see
d0e0b50dff42) — reports are designed immutable, and nothing in the application mutated one after
creation. PDF export changes that in one narrow way: a report's ``exported_uri`` starts null and
is set once, lazily, the first time its PDF is downloaded
(``ReportingService.get_or_export_report_pdf``); everything else about a report
(``body_markdown``, ``finding_ids``, ``version``, ...) remains write-once-at-creation.

Column-scoped ``GRANT UPDATE (exported_uri)``, not a blanket ``GRANT UPDATE``, matches the
least-privilege pattern d0e0b50dff42 already used for ``reporting.findings``'s disposition
columns — the database itself enforces that no code path can rewrite a report's actual content,
not just the application layer choosing not to.

Revision ID: 9f3b6a1c2d47
Revises: 4d080c5f1bcc
Create Date: 2026-07-14 11:45:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3b6a1c2d47"
down_revision: str | None = "4d080c5f1bcc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"


def upgrade() -> None:
    op.execute(f"GRANT UPDATE (exported_uri) ON reporting.reports TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE (exported_uri) ON reporting.reports FROM {_APP_ROLE}")
