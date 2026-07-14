"""identity.credentials: local email/password signup, alongside existing Entra JIT provisioning

Entra ID (via ``identity.users.entra_object_id`` + JIT provisioning) remains the enterprise SSO
path this schema was originally built for. This migration adds a second, parallel identity path
for self-service signup — an organization onboarding onto this platform before its Entra
integration is wired up still needs a real way to create an account, not a login screen that only
lists pre-seeded demo personas.

A locally-registered user is still just an ``identity.users`` row: ``entra_object_id`` is set to a
synthetic ``local:<uuid>`` value (unique, never collides with a real Entra object id, and is an
honest marker of "this identity was locally registered" rather than a fabricated-looking Entra id).
Everything downstream — JIT provisioning, RLS, engagement membership, roster visibility — already
works on ``identity.users``/``identity.engagement_members`` and needs no changes.

``identity.credentials`` holds only what login needs: a bcrypt hash, one row per user (enforced by
a unique FK), cascade-deleted if the user row is ever removed. A partial unique index on
``identity.users.email`` (``WHERE email <> ''``) enforces "one account per email" for real
addresses without breaking existing JIT-provisioned rows that share the empty-string placeholder
email (``get_current_db_user``'s honest-placeholder value for unenriched Entra identities).

Also widens ``identity.engagement_members`` from SELECT-only (the original migration's deliberate
scope — "no code path writes here yet") to also allow INSERT, guarded by a new RLS policy scoped
the same way the existing self-only SELECT policy is: a row may only be inserted with
``user_id = current_setting('app.current_user_id')``, i.e. a caller can only ever grant *themselves*
membership, never someone else. Self-service signup is the first write path: it binds the RLS
context to the newly-created user (the same ``set_rls_user_context`` call every authenticated
request already makes) before inserting that user's own membership row, so this is the identical
enforcement mechanism as every other RLS-protected write in this codebase, not a special case.

Revision ID: d4a19e6b7f31
Revises: 7438c0403014
Create Date: 2026-07-13 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "d4a19e6b7f31"
down_revision: str | None = "7438c0403014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "auditmind_app"


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="identity",
    )
    op.execute(f"GRANT SELECT, INSERT ON identity.credentials TO {_APP_ROLE}")

    # One real email per account. Partial so the many pre-existing/legacy rows that share the
    # empty-string placeholder email (Entra identities never enriched via Graph) don't collide.
    op.execute(
        "CREATE UNIQUE INDEX uq_users_email_nonempty ON identity.users (email) WHERE email <> ''"
    )

    # Self-service signup's one write path: a caller may insert a membership row for themselves
    # (and only themselves) into an engagement. Additive to, not a replacement of, the existing
    # SELECT-only grant/policies above.
    op.execute(f"GRANT INSERT ON identity.engagement_members TO {_APP_ROLE}")
    op.execute(
        """
        CREATE POLICY engagement_members_self_insert ON identity.engagement_members
        FOR INSERT
        WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS engagement_members_self_insert ON identity.engagement_members"
    )
    op.execute("DROP INDEX IF EXISTS identity.uq_users_email_nonempty")
    op.drop_table("credentials", schema="identity")
