"""agent schema: runs, checkpoints, hitl_interrupts

The persistence layer for the 9-agent LangGraph runtime (services/agent-orchestrator, ADR-001).
Three tables:

  * ``agent.runs``            — one row per graph invocation (id, engagement_id, use_case, status,
                                initiated_by, task).
  * ``agent.checkpoints``     — the LangGraph checkpointer's durable state snapshots (run_id fk,
                                step_name, state jsonb) — the full replay/resume source. Written by
                                the checkpointer, not by application code.
  * ``agent.hitl_interrupts`` — one row per human checkpoint (run_id fk, step_name, decision,
                                reviewer_id, reason) — the interrupt/resume state machine's audit
                                record.

Reuses the subquery-based Row-Level Security pattern from apps/api's identity migrations: a user
may act on a row iff they are presently a member of that row's engagement, checked against the
actual ``identity.engagement_members`` table. ``engagement_id`` is denormalized onto every table so
each policy is a single-hop check, not a join through ``agent.runs``.

This service runs against the same physical database as apps/api, so the FKs into ``identity.*``
and the grants to the shared least-privilege ``auditmind_app`` role both resolve — apps/api's
identity migration must have run first (see migrations/README).

Revision ID: e1f2a3b4c5d6
Revises:
Create Date: 2026-07-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The least-privilege role the running service connects as — created by apps/api's identity
# migration, granted access to the agent schema here. Never the migration/admin role, so RLS
# policies actually apply to the running service.
_APP_ROLE = "auditmind_app"

# The same subquery apps/api's identity migrations already use, applied to each agent table's own
# denormalized ``engagement_id``. ``{table}`` is substituted per policy so the subquery references
# the correct table's column.
_MEMBERSHIP_CHECK_TEMPLATE = """
    EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = {table}.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _engagement_fk() -> sa.Column:
    return sa.Column(
        "engagement_id",
        UUID(as_uuid=True),
        sa.ForeignKey("identity.engagements.id"),
        nullable=False,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _enable_rls(table: str) -> None:
    """Enable + force RLS on ``agent.{table}`` and install the engagement-member-only policy.

    ``FORCE`` matters: without it, the table owner (the migration/admin role) bypasses RLS, which
    would make the policy appear to work while the app role — the only role that ever connects at
    runtime — is the one it actually constrains. Same discipline every prior RLS migration uses.
    """
    check = _MEMBERSHIP_CHECK_TEMPLATE.format(table=table)
    op.execute(f"ALTER TABLE agent.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE agent.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_engagement_member_only ON agent.{table}
        USING ({check})
        """
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")

    op.create_table(
        "runs",
        _uuid_pk(),
        _engagement_fk(),
        sa.Column("use_case", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "initiated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.users.id"),
            nullable=False,
        ),
        sa.Column("task", sa.Text(), nullable=False),
        _created_at(),
        schema="agent",
    )
    op.create_index("ix_agent_runs_engagement_id", "runs", ["engagement_id"], schema="agent")

    op.create_table(
        "checkpoints",
        _uuid_pk(),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("agent.runs.id"), nullable=False
        ),
        _engagement_fk(),
        sa.Column("step_name", sa.String(), nullable=False),
        sa.Column("state", JSONB(), nullable=False),
        _created_at(),
        schema="agent",
    )
    op.create_index(
        "ix_agent_checkpoints_run_id", "checkpoints", ["run_id"], schema="agent"
    )
    op.create_index(
        "ix_agent_checkpoints_engagement_id", "checkpoints", ["engagement_id"], schema="agent"
    )

    op.create_table(
        "hitl_interrupts",
        _uuid_pk(),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("agent.runs.id"), nullable=False
        ),
        _engagement_fk(),
        sa.Column("step_name", sa.String(), nullable=False),
        # NULL decision distinguishes an open interrupt from a resolved one — no separate status
        # column, the same append-then-resolve shape apps/api's finding disposition uses.
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.users.id"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        _created_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema="agent",
    )
    op.create_index(
        "ix_agent_hitl_interrupts_run_id", "hitl_interrupts", ["run_id"], schema="agent"
    )
    op.create_index(
        "ix_agent_hitl_interrupts_engagement_id",
        "hitl_interrupts",
        ["engagement_id"],
        schema="agent",
    )

    # Least-privilege grants. The service inserts runs, advances their status (UPDATE on runs),
    # records checkpoints, and opens/resolves interrupts (UPDATE on hitl_interrupts) — so runs and
    # hitl_interrupts get SELECT/INSERT/UPDATE, checkpoints get SELECT/INSERT only (a checkpoint is
    # an immutable snapshot; the checkpointer never updates a written one). No DELETE grant
    # anywhere: a run's history, its checkpoints, and its human decisions are retained for
    # governance replay, never deleted by the running service.
    op.execute(f"GRANT USAGE ON SCHEMA agent TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON agent.runs TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON agent.checkpoints TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON agent.hitl_interrupts TO {_APP_ROLE}")

    _enable_rls("runs")
    _enable_rls("checkpoints")
    _enable_rls("hitl_interrupts")


def downgrade() -> None:
    for table in ("hitl_interrupts", "checkpoints", "runs"):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_engagement_member_only ON agent.{table}"
        )
    op.drop_table("hitl_interrupts", schema="agent")
    op.drop_table("checkpoints", schema="agent")
    op.drop_table("runs", schema="agent")
    # The `agent` schema is left in place — same restraint every prior migration's downgrade shows
    # toward schemas (a schema is where this context's tables live going forward; dropping it is a
    # blast-radius decision a per-migration downgrade shouldn't make).
