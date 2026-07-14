"""Integration tests proving Row-Level Security actually isolates users at the database level —
not merely that application code remembers to filter.

Two separate connections are used deliberately:

- ``admin_engine`` connects as the migration/superuser role and is used *only* to seed and clean
  up fixture data — never to run the assertions under test, since a superuser bypasses RLS and a
  test that queried through it would prove nothing.
- ``app_engine`` connects as ``auditmind_app``, the exact least-privilege role the running FastAPI
  application uses (``shared/database.py``). Every assertion in this file queries through this
  engine, so a passing test is proof the same isolation the application relies on in production
  actually holds.

Requires a real, reachable Postgres — skips automatically if ``AUDITMIND_MIGRATION_DATABASE_URL``
isn't set, so the rest of the suite still runs in an environment with no database available.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("AUDITMIND_MIGRATION_DATABASE_URL"),
    reason="Requires a real Postgres instance — set AUDITMIND_MIGRATION_DATABASE_URL to run.",
)


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["AUDITMIND_MIGRATION_DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    host = os.environ.get("AUDITMIND_TEST_DB_HOST", "localhost")
    port = os.environ.get("AUDITMIND_TEST_DB_PORT", "5433")
    name = os.environ.get("AUDITMIND_TEST_DB_NAME", "auditmind_dev")
    user = os.environ.get("AUDITMIND_TEST_APP_USER", "auditmind_app")
    password = os.environ.get("AUDITMIND_TEST_APP_PASSWORD", "auditmind_app_local_dev")
    url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def two_users_two_engagements(
    admin_engine: AsyncEngine,
) -> AsyncIterator[dict[str, str]]:
    """Seeds one tenant, two engagements, two users, and one membership row each — user A is a
    member of engagement 1 only; user B is a member of engagement 2 only. Cleaned up afterward
    regardless of test outcome."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_b": str(uuid.uuid4()),
    }
    entra_suffix = uuid.uuid4().hex[:8]

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Test Tenant')"),
            {"id": ids["tenant"]},
        )
        for key in ("engagement_1", "engagement_2"):
            await conn.execute(
                text(
                    "INSERT INTO identity.engagements (id, tenant_id, name) "
                    "VALUES (:id, :tenant_id, :name)"
                ),
                {"id": ids[key], "tenant_id": ids["tenant"], "name": f"Engagement {key}"},
            )
        for key in ("user_a", "user_b"):
            await conn.execute(
                text(
                    "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                    "VALUES (:id, :entra_oid, :name, :email)"
                ),
                {
                    "id": ids[key],
                    "entra_oid": f"entra-{key}-{entra_suffix}",
                    "name": key,
                    # Suffixed (like entra_object_id above) so this collides with neither a
                    # concurrent run of this same fixture nor `two_users_same_engagement`'s
                    # "user_a" below — `identity.users.email` has a real uniqueness constraint
                    # (migration d4a19e6b7f31) now that self-service signup exists.
                    "email": f"{key}-{entra_suffix}@example.com",
                },
            )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["engagement_1"], "user_id": ids["user_a"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'FraudAnalyst')"
            ),
            {"engagement_id": ids["engagement_2"], "user_id": ids["user_b"]},
        )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE id IN (:a, :b)"),
            {"a": ids["user_a"], "b": ids["user_b"]},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id IN (:e1, :e2)"),
            {"e1": ids["engagement_1"], "e2": ids["engagement_2"]},
        )
        await conn.execute(
            text("DELETE FROM identity.tenants WHERE id = :id"), {"id": ids["tenant"]}
        )


async def test_admin_connection_can_see_both_membership_rows(
    admin_engine: AsyncEngine, two_users_two_engagements: dict[str, str]
) -> None:
    """Sanity check on the fixture itself: the seeded data really has two rows before we prove
    the app role can't see both of them."""
    async with admin_engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM identity.engagement_members"))
        assert result.scalar_one() == 2


async def test_app_role_sees_only_the_row_matching_its_rls_context(
    app_engine: AsyncEngine, two_users_two_engagements: dict[str, str]
) -> None:
    """The central proof: connected as the exact least-privilege role the application uses, with
    the RLS context set to user A, a bare `SELECT *` — no `WHERE user_id = ...` clause at all —
    returns only user A's row. User B's row exists in the table but is invisible."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_users_two_engagements["user_a"]},
        )
        result = await conn.execute(
            text("SELECT engagement_id, user_id FROM identity.engagement_members")
        )
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].user_id) == two_users_two_engagements["user_a"]
    assert str(rows[0].engagement_id) == two_users_two_engagements["engagement_1"]


async def test_app_role_cannot_see_another_users_row_even_when_explicitly_queried_by_id(
    app_engine: AsyncEngine, two_users_two_engagements: dict[str, str]
) -> None:
    """The harder case: even a query that explicitly filters `WHERE user_id = <user B's id>` —
    exactly the kind of bug an application-layer-only check could plausibly ship — returns
    nothing, because RLS applies before the WHERE clause's result is ever returned to the caller.
    This is what "the database enforces it, not just the application" actually means in practice.
    """
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_users_two_engagements["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM identity.engagement_members WHERE user_id = :other_user_id"),
            {"other_user_id": two_users_two_engagements["user_b"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_without_any_rls_context_sees_nothing(
    app_engine: AsyncEngine, two_users_two_engagements: dict[str, str]
) -> None:
    """If `set_rls_user_context` were ever skipped by a bug elsewhere in the request pipeline,
    the fail-safe behavior is seeing zero rows, not every row — `current_setting(..., true)`
    returns NULL when unset, and `user_id = NULL` is never true in SQL, so it fails closed."""
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM identity.engagement_members"))
        rows = result.all()

    assert rows == []


@pytest_asyncio.fixture
async def two_users_same_engagement(
    admin_engine: AsyncEngine,
) -> AsyncIterator[dict[str, str]]:
    """Seeds one tenant, one engagement, and two users both members of it — the fixture the
    roster-visibility policy's tests need, distinct from `two_users_two_engagements` above (which
    deliberately keeps its two users in *different* engagements to prove isolation, not roster
    visibility)."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_c": str(uuid.uuid4()),
    }
    entra_suffix = uuid.uuid4().hex[:8]

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Test Tenant')"),
            {"id": ids["tenant"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagements (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'Roster Test Engagement')"
            ),
            {"id": ids["engagement"], "tenant_id": ids["tenant"]},
        )
        for key in ("user_a", "user_c"):
            await conn.execute(
                text(
                    "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                    "VALUES (:id, :entra_oid, :name, :email)"
                ),
                {
                    "id": ids[key],
                    "entra_oid": f"entra-{key}-{entra_suffix}",
                    "name": key,
                    "email": f"{key}-{entra_suffix}@example.com",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                    "VALUES (:engagement_id, :user_id, 'Auditor')"
                ),
                {"engagement_id": ids["engagement"], "user_id": ids[key]},
            )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE id IN (:a, :c)"),
            {"a": ids["user_a"], "c": ids["user_c"]},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id = :id"), {"id": ids["engagement"]}
        )
        await conn.execute(
            text("DELETE FROM identity.tenants WHERE id = :id"), {"id": ids["tenant"]}
        )


async def test_member_sees_the_full_roster_of_their_own_engagement(
    app_engine: AsyncEngine, two_users_same_engagement: dict[str, str]
) -> None:
    """The roster-visibility policy's central proof: user A, querying with no explicit filter at
    all, sees BOTH members of the engagement they belong to — not just their own row, which is
    what the pre-existing `engagement_members_self_only` policy alone would have limited them to.
    """
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_users_same_engagement["user_a"]},
        )
        result = await conn.execute(
            text(
                "SELECT user_id FROM identity.engagement_members WHERE engagement_id = :eng_id"
            ),
            {"eng_id": two_users_same_engagement["engagement"]},
        )
        seen_user_ids = {str(row.user_id) for row in result.all()}

    assert seen_user_ids == {
        two_users_same_engagement["user_a"],
        two_users_same_engagement["user_c"],
    }


async def test_member_cannot_see_another_engagements_roster(
    app_engine: AsyncEngine, two_users_two_engagements: dict[str, str]
) -> None:
    """Roster visibility is scoped to engagements the caller actually belongs to — it must not
    become a blanket "any member can see any engagement's roster" policy. User A (engagement 1
    only) querying explicitly for engagement 2's roster sees nothing, even though user B's row for
    engagement 2 exists in the table."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_users_two_engagements["user_a"]},
        )
        result = await conn.execute(
            text(
                "SELECT user_id FROM identity.engagement_members WHERE engagement_id = :eng_id"
            ),
            {"eng_id": two_users_two_engagements["engagement_2"]},
        )
        rows = result.all()

    assert rows == []
