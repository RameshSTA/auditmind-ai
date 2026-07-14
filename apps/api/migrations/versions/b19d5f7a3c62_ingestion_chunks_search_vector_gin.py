"""ingestion.chunks: search_vector generated column + GIN index (Increment 07, Phase 6 §10)

Adds the "keyword leg" of the Retrieval Agent: a generated ``tsvector`` column computed from
``ingestion.chunks.text`` by Postgres itself on every insert (``GENERATED ALWAYS AS ... STORED`` —
never written by application code, so it can never drift out of sync with ``text``), backed by a
GIN index for fast full-text search.

This migration does not create a new schema or grant anything new: ``auditmind_app`` already holds
table-level ``SELECT`` on ``ingestion.chunks`` from Increment 03, and a table-level grant covers
columns added later by ``ALTER TABLE ... ADD COLUMN`` automatically — no new `GRANT` statement is
needed for the new `retrieval` context to read this column.

Revision ID: b19d5f7a3c62
Revises: a7c4e912f568
Create Date: 2026-07-10 17:45:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b19d5f7a3c62"
down_revision: str | None = "a7c4e912f568"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ingestion.chunks "
        "ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_chunks_search_vector ON ingestion.chunks USING GIN (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ingestion.ix_chunks_search_vector")
    op.execute("ALTER TABLE ingestion.chunks DROP COLUMN search_vector")
