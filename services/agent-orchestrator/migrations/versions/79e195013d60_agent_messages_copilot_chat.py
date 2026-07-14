"""agent schema: messages (AI Copilot conversational chat)

The persistence layer for AI Copilot's chat thread — one row per turn, private per
``(engagement_id, user_id)``. Distinct from ``agent.runs``/``agent.hitl_interrupts``: a chat
message is a lightweight conversational turn, not a graph invocation; when a turn hands off to a
real investigation, ``run_id`` points at the (unmodified) run that pipeline creates, rather than
duplicating any of its state here.

RLS is intentionally different from every other ``agent.*`` table: those are engagement-shared
(any member sees every run), but a chat thread is a personal working session, not a team record —
the policy checks membership *and* ``user_id = current_setting('app.current_user_id')``, so a
message is visible only to the user who sent or received it, never to a teammate on the same
engagement.

Revision ID: 79e195013d60
Revises: e1f2a3b4c5d6
Create Date: 2026-07-14 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "79e195013d60"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"

# Both the engagement-membership check every other agent.* table's policy uses AND a user_id
# equality check — a message is private to the one user in the conversation, not shared with every
# engagement member the way a run or a HITL interrupt is.
_MESSAGE_POLICY_CHECK = """
    user_id = current_setting('app.current_user_id', true)::uuid
    AND EXISTS (
        SELECT 1 FROM identity.engagement_members em
        WHERE em.engagement_id = messages.engagement_id
          AND em.user_id = current_setting('app.current_user_id', true)::uuid
    )
"""


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.engagements.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("identity.users.id"), nullable=False
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=False, server_default="text"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent.runs.id"), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="agent",
    )
    op.create_index(
        "ix_agent_messages_engagement_id", "messages", ["engagement_id"], schema="agent"
    )
    op.create_index("ix_agent_messages_user_id", "messages", ["user_id"], schema="agent")
    op.create_index("ix_agent_messages_run_id", "messages", ["run_id"], schema="agent")

    # SELECT/INSERT only, same as agent.checkpoints — a message is an immutable turn, never
    # updated or deleted once written (a conversation only ever grows).
    op.execute(f"GRANT SELECT, INSERT ON agent.messages TO {_APP_ROLE}")

    op.execute("ALTER TABLE agent.messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent.messages FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY messages_own_and_engagement_member_only ON agent.messages
        USING ({_MESSAGE_POLICY_CHECK})
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS messages_own_and_engagement_member_only ON agent.messages"
    )
    op.drop_table("messages", schema="agent")
