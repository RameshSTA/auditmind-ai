"""identity schema: users, tenants, engagements, engagement_members, RLS

Implements the ``identity`` schema, the least-privilege application role, and the first concrete
Row-Level Security policy — applied to ``engagement_members`` because it is the one identity table
where "a user can only see their own row" is a meaningful, testable isolation guarantee before any
engagement-scoped evidence tables exist to protect.

Revision ID: a0bf8e859800
Revises:
Create Date: 2026-07-10 12:19:23.263045

"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "a0bf8e859800"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"


def _uuid_pk_column() -> sa.Column:
    """The ``id UUID PRIMARY KEY DEFAULT gen_random_uuid()`` shape shared by every table below."""
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")

    # The least-privilege role the running application connects as, created idempotently so a
    # fresh environment running migrations from scratch needs no manual pre-step. Its password is
    # sourced from AUDITMIND_APP_DB_PASSWORD and applied here — schema migrations do not usually
    # own a credential's lifecycle, but no separate secrets-bootstrap tooling exists yet (that
    # arrives with Key Vault integration); until then, one idempotent, reproducible step is safer
    # than a manual `ALTER ROLE` an operator can forget.
    # Environments without the variable set get a role that exists but cannot authenticate via
    # password — acceptable where `trust` authentication is configured for local-only connections
    # (this repo's local dev instructions), but not for CI or production, both of which must set it.
    app_role_password = os.environ.get("AUDITMIND_APP_DB_PASSWORD")
    op.execute(
        f"""
        DO $$
        BEGIN
           IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{_APP_ROLE}') THEN
              CREATE ROLE {_APP_ROLE} LOGIN;
           END IF;
        END
        $$;
        """
    )
    if app_role_password:
        # `ALTER ROLE ... PASSWORD` does not accept a bind parameter (verified against a live
        # instance — PostgreSQL rejects `$1` there with a syntax error, because ALTER ROLE is a
        # utility statement, not a parameterizable query). `quote_literal()` is a normal SQL
        # function and *does* accept a bind parameter, so the password is safely escaped
        # server-side first, and only the already-quoted, already-safe result is interpolated
        # into the ALTER ROLE text — proven against a password containing a quote and a `;--`
        # injection attempt during development of this migration.
        bind = op.get_bind()
        quoted_password = bind.execute(
            sa.text("SELECT quote_literal(:pw)"), {"pw": app_role_password}
        ).scalar_one()
        op.execute(f"ALTER ROLE {_APP_ROLE} PASSWORD {quoted_password}")

    op.create_table(
        "users",
        _uuid_pk_column(),
        sa.Column("entra_object_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        _created_at_column(),
        schema="identity",
    )
    op.create_unique_constraint(
        "uq_users_entra_object_id", "users", ["entra_object_id"], schema="identity"
    )

    op.create_table(
        "tenants",
        _uuid_pk_column(),
        sa.Column("name", sa.String(), nullable=False),
        _created_at_column(),
        schema="identity",
    )

    op.create_table(
        "engagements",
        _uuid_pk_column(),
        sa.Column(
            "tenant_id", UUID(as_uuid=True), sa.ForeignKey("identity.tenants.id"), nullable=False
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("business_unit", sa.String(), nullable=False, server_default=""),
        sa.Column("sensitivity_tier", sa.String(), nullable=False, server_default="internal"),
        sa.Column("status", sa.String(), nullable=False, server_default="planning"),
        _created_at_column(),
        schema="identity",
    )

    op.create_table(
        "engagement_members",
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.engagements.id"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("identity.users.id"), primary_key=True
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="identity",
    )

    # Least-privilege grants — the app role gets exactly what it needs and nothing else: it must
    # be able to read tenants/engagements, and read/write its own provisioned users and engagement
    # memberships. It has no DDL rights at all.
    op.execute(f"GRANT USAGE ON SCHEMA identity TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON identity.users TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT ON identity.tenants TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT ON identity.engagements TO {_APP_ROLE}")
    # SELECT only — no code path here writes to engagement_members (membership assignment is a
    # future admin feature), so the app role is granted exactly what it uses today, not what it
    # might need eventually.
    op.execute(f"GRANT SELECT ON identity.engagement_members TO {_APP_ROLE}")

    # Row-Level Security: a user may only ever *see* their own membership rows. FORCE ROW LEVEL
    # SECURITY matters here because `auditmind_app` does not own this table (the migration role
    # does) — without FORCE, RLS is only bypassed by superusers and the table owner, but FORCE
    # makes the policy apply even to a role that somehow gains owner-equivalent grants later,
    # which is the safer default for a table whose entire purpose is access control.
    #
    # Scoped to `FOR SELECT` only, deliberately: no membership-assignment feature exists yet (an
    # Admin/CAE granting a *different* user access to an engagement), so a write-side policy would
    # have nothing legitimate to validate against yet — a self-only USING clause applied to
    # INSERT/UPDATE as well would block that entirely legitimate future admin action (Admin's own
    # current_user_id is never the invitee's id). Membership assignment, once built, should add
    # its own, separate write-path policy (or a privileged SECURITY DEFINER function) rather than
    # retrofit this one.
    op.execute("ALTER TABLE identity.engagement_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity.engagement_members FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY engagement_members_self_only ON identity.engagement_members
        FOR SELECT
        USING (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS engagement_members_self_only ON identity.engagement_members")
    op.drop_table("engagement_members", schema="identity")
    op.drop_table("engagements", schema="identity")
    op.drop_table("tenants", schema="identity")
    op.drop_constraint("uq_users_entra_object_id", "users", schema="identity", type_="unique")
    op.drop_table("users", schema="identity")
    # Deliberately does not DROP ROLE auditmind_app or DROP SCHEMA identity — a role/schema drop
    # is a destructive, cross-migration-history action that could strand grants from a later
    # migration; those are cleaned up manually if this whole context is ever fully decommissioned.
