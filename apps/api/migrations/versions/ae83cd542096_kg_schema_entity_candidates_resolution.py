"""kg schema: entity_candidates, entity_resolution_map (Increment 09, Phase 4 §3)

The Postgres side of the Knowledge Graph context — Neo4j itself has no migration story (Increment
09's application code creates/merges graph nodes idempotently at write time, the same "let the
write be self-describing" approach every Cypher `MERGE` already implies). These two tables are the
"Postgres → Neo4j bridge" Phase 4 §4 describes: `entity_resolution_map` is the only table allowed
to hold a Neo4j node id, so nothing else in this codebase needs to know the graph exists to
reference a vendor.

**Deviation from Phase 4 §2's table, documented deliberately**: the design lists
`entity_candidates.source_chunk_id` (a candidate sourced from NLP-extracted document text). This
increment sources candidates from `risk.transactions.raw_payload`'s `vendor_name` key instead —
`risk/domain/entities.py`'s own `Transaction.raw_payload` docstring already flagged this as the
plan ("entity resolution ... needs Neo4j, which this increment's environment doesn't have ... until
that exists"). Free-text NLP/NER entity extraction needs a model decision this increment doesn't
make (the same LiteLLM-gateway/local-model tradeoff Increment 08 already navigated for embeddings);
extracting a vendor identity from a transaction's own structured `vendor_name` field needs neither
an LLM nor a new library. `source_transaction_id` replaces `source_chunk_id`; the column exists to
be extended (nullable) once a chunk-sourced candidate path is built.

Reuses two RLS patterns already established: the subquery pattern (Increments 03-05, `risk`
Increment 05 among them) for `entity_candidates`, which has its own `engagement_id`; the join-based
pattern (`reporting.report_findings`, Increment 04) for `entity_resolution_map`, which — like that
table — has no `engagement_id` column of its own and joins through `entity_candidates` to find one.

Revision ID: ae83cd542096
Revises: cf444e7a5393
Create Date: 2026-07-10 23:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "ae83cd542096"
down_revision: str | None = "cf444e7a5393"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

_MEMBERSHIP_CHECK = """
    EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = entity_candidates.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""


def _uuid_pk_column() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _uuid_fk_column(name: str, *, references: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, UUID(as_uuid=True), sa.ForeignKey(references), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS kg")

    op.create_table(
        "entity_candidates",
        _uuid_pk_column(),
        _uuid_fk_column("engagement_id", references="identity.engagements.id"),
        _uuid_fk_column(
            "source_transaction_id", references="risk.transactions.id", nullable=True
        ),
        # Free text, not an enum — 'vendor' is the only value this increment ever writes
        # (Employee/Account/Contract candidates need data this codebase doesn't ingest yet, see
        # the increment doc's deferred section), but the column shouldn't need a migration to add
        # a second entity type later.
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("raw_name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        # Always written as 'resolved' at insert time by this increment — resolution happens
        # synchronously in the same call that creates the candidate (no job queue exists, see
        # Increment 08's precedent for the same "no async infra yet" scope decision), so a
        # 'pending' state is never actually persisted. The column stays free text, matching Phase
        # 4 §2's schema, so a future async resolution path doesn't need a migration to introduce
        # 'pending'/'merged'/'rejected' as real, observed values.
        sa.Column("status", sa.String(), nullable=False, server_default="resolved"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="kg",
    )
    op.create_index(
        "ix_entity_candidates_engagement_id", "entity_candidates", ["engagement_id"], schema="kg"
    )
    # One candidate per (transaction, entity_type) — re-running resolution over the same
    # engagement is idempotent at the database level via ON CONFLICT DO NOTHING, not just an
    # application-level pre-check (the same discipline Increment 03's sha256 dedup constraint and
    # Increment 08's (chunk_id, model_id) primary key both already established).
    op.create_unique_constraint(
        "uq_entity_candidates_transaction_entity_type",
        "entity_candidates",
        ["source_transaction_id", "entity_type"],
        schema="kg",
    )

    op.create_table(
        "entity_resolution_map",
        _uuid_fk_column(
            "candidate_id", references="kg.entity_candidates.id", nullable=False
        ),
        sa.Column("neo4j_entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        # Nullable: automated normalized-exact-match resolution (this increment's only mode, the
        # same "no fuzzy matching yet" scope Increment 05 drew for duplicate-payment detection)
        # has no human resolver. A human-reviewed merge/split would set this.
        _uuid_fk_column("resolved_by", references="identity.users.id", nullable=True),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("candidate_id"),
        schema="kg",
    )
    op.create_index(
        "ix_entity_resolution_map_neo4j_entity_id",
        "entity_resolution_map",
        ["neo4j_entity_id"],
        schema="kg",
    )

    # Least-privilege grants (Phase 11 §7/§8). Both tables are insert-only from the app role's
    # perspective — nothing in this increment ever mutates a row after it's written, the same
    # shape as `risk.transactions`.
    op.execute(f"GRANT USAGE ON SCHEMA kg TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON kg.entity_candidates TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON kg.entity_resolution_map TO {_APP_ROLE}")

    op.execute("ALTER TABLE kg.entity_candidates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE kg.entity_candidates FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY entity_candidates_engagement_member_only ON kg.entity_candidates
        USING ({_MEMBERSHIP_CHECK})
        """
    )

    op.execute("ALTER TABLE kg.entity_resolution_map ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE kg.entity_resolution_map FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY entity_resolution_map_engagement_member_only ON kg.entity_resolution_map
        USING (
            EXISTS (
                SELECT 1 FROM kg.entity_candidates ec
                JOIN identity.engagement_members em ON em.engagement_id = ec.engagement_id
                WHERE ec.id = entity_resolution_map.candidate_id
                  AND em.user_id = current_setting('app.current_user_id', true)::uuid
            )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS entity_resolution_map_engagement_member_only "
        "ON kg.entity_resolution_map"
    )
    op.execute(
        "DROP POLICY IF EXISTS entity_candidates_engagement_member_only ON kg.entity_candidates"
    )
    op.drop_table("entity_resolution_map", schema="kg")
    op.drop_table("entity_candidates", schema="kg")
