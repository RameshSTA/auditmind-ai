"""retrieval schema: chunk_embeddings (pgvector)

The vector-embedding leg of retrieval, deferred until pgvector was available — this is where
that work continues. ``retrieval`` gets its own schema and its first owned table; until now
it only ever read a column ``ingestion.chunks`` owns (``search_vector``).

Schema fixed as: ``(chunk_id, model_id)`` composite primary key so a model upgrade adds new rows
rather than overwriting old embeddings (they coexist until an evaluation harness confirms the new
model is an improvement; that harness is not built yet). ``engagement_id`` is denormalized onto
this table (rather than requiring a join through ``ingestion.chunks`` to know it) for the same
reason every other table in this codebase denormalizes it: RLS policies and query filters both
need it in one hop.

Reuses the subquery-based Row-Level Security pattern established elsewhere — this table has a
genuine write path (batch embedding generation), the same reasoning that pattern was chosen for
every other write-path table so far.

Revision ID: cf444e7a5393
Revises: b19d5f7a3c62
Create Date: 2026-07-10 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "cf444e7a5393"
down_revision: str | None = "b19d5f7a3c62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

# Same subquery pattern used elsewhere — a user may act on a row if and only if they are
# presently a member of that row's engagement, checked against the actual membership table.
_MEMBERSHIP_CHECK = """
    EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = chunk_embeddings.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""

# BGE-M3's dense output dimension — fixed by the chosen model, not configurable.
_EMBEDDING_DIM = 1024


def upgrade() -> None:
    # Idempotent and safe to run against a database that already has it (e.g. from manual
    # exploration) — CREATE EXTENSION IF NOT EXISTS is itself transactional/re-runnable.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS retrieval")

    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingestion.chunks.id"),
            primary_key=True,
        ),
        # Not a foreign key to a "models" table — no such registry exists yet (Governance & Admin
        # context, unimplemented). A plain identifying string for now, e.g. "BAAI/bge-m3".
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.engagements.id"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="retrieval",
    )
    op.create_index(
        "ix_chunk_embeddings_engagement_id",
        "chunk_embeddings",
        ["engagement_id"],
        schema="retrieval",
    )
    # HNSW + cosine ops — m=16, ef_construction=64 are the pgvector-recommended starting values,
    # to be revisited once real corpus size and query-latency data exist.
    op.execute(
        "CREATE INDEX chunk_embeddings_hnsw_idx ON retrieval.chunk_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # Least-privilege grants. SELECT for search, INSERT for embedding generation — no UPDATE or
    # DELETE grant, matching this table's actual access pattern: a re-embed with an
    # existing model_id is idempotent via ON CONFLICT DO NOTHING at the application layer (see
    # retrieval/infrastructure/embedding_index.py), never an UPDATE of an existing row's vector.
    op.execute(f"GRANT USAGE ON SCHEMA retrieval TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON retrieval.chunk_embeddings TO {_APP_ROLE}")

    op.execute("ALTER TABLE retrieval.chunk_embeddings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE retrieval.chunk_embeddings FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY chunk_embeddings_engagement_member_only ON retrieval.chunk_embeddings
        USING ({_MEMBERSHIP_CHECK})
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS chunk_embeddings_engagement_member_only "
        "ON retrieval.chunk_embeddings"
    )
    op.drop_table("chunk_embeddings", schema="retrieval")
    # The `vector` extension and `retrieval` schema are left in place — dropping either is a
    # blast-radius decision (the extension may be relied on elsewhere, the schema is where this
    # context's tables live going forward) that a per-table downgrade shouldn't make unilaterally,
    # the same restraint every prior migration's downgrade already shows toward schemas.
