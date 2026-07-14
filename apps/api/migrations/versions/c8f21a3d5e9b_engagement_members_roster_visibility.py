"""identity.engagement_members: engagement-roster RLS visibility (Administration, read-only)

Adds a second, additive `FOR SELECT` policy on top of the `engagement_members_self_only` policy
the identity schema migration created (Phase 4 §12): a caller may now see every membership row for
an engagement they themselves belong to, not just their own row. Postgres OR's multiple permissive
policies together for the same command, so this strictly *widens* visibility from "your own row"
to "your own row, plus every other row in engagements you're in" — it does not replace or weaken
the existing policy, and cross-engagement isolation is unchanged: a member of engagement A still
sees nothing about engagement B unless they're also a member of B (proven in
`test_identity_rls.py`'s new roster tests).

**Why a SECURITY DEFINER function instead of a direct subquery**: the obvious-looking policy —
`USING (engagement_id IN (SELECT engagement_id FROM identity.engagement_members WHERE user_id =
current_setting(...)))` — fails at query time with `InvalidObjectDefinitionError: infinite
recursion detected in policy for relation "engagement_members"` (confirmed against a live
instance before writing this comment, not assumed from documentation). Postgres re-applies a
table's RLS policies to a subquery that references that same table from within its own policy,
which recurses. The standard, documented fix for exactly this "which rows share a group with me"
pattern is a `SECURITY DEFINER` helper function: it runs as its *owner* (this migration's role,
`auditmind`, a real Postgres superuser — confirmed via `pg_roles.rolsuper`), and superusers always
bypass RLS regardless of `FORCE ROW LEVEL SECURITY` (only the table-owner exemption is affected by
FORCE; the superuser/BYPASSRLS exemption is separate and unconditional). The function's `SET
search_path` pins name resolution so it can't be hijacked by a session-level `search_path` change
— standard hardening for any `SECURITY DEFINER` routine, not specific to this table.

This is exactly the "future admin feature" the original identity migration's docstring named and
deliberately declined to build: a read-only engagement roster (Administration §"who has access"),
not membership assignment (inviting/adding/removing a member), which stays a separate, unbuilt
write-path policy. No table grants change — `auditmind_app` already had SELECT on this table; it
additionally needs, and is granted, EXECUTE on the new function.

Revision ID: c8f21a3d5e9b
Revises: 9910da8bbd2c
Create Date: 2026-07-13 08:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f21a3d5e9b"
down_revision: str | None = "9910da8bbd2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION identity.current_user_engagement_ids()
        RETURNS SETOF uuid
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        SET search_path = identity, pg_temp
        AS $$
            SELECT engagement_id FROM identity.engagement_members
            WHERE user_id = current_setting('app.current_user_id', true)::uuid
        $$
        """
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION identity.current_user_engagement_ids() TO {_APP_ROLE}"
    )
    op.execute(
        """
        CREATE POLICY engagement_members_engagement_roster ON identity.engagement_members
        FOR SELECT
        USING (engagement_id IN (SELECT identity.current_user_engagement_ids()))
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS engagement_members_engagement_roster "
        "ON identity.engagement_members"
    )
    op.execute("DROP FUNCTION IF EXISTS identity.current_user_engagement_ids()")
