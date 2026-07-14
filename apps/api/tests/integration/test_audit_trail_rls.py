"""Integration tests proving two independent guarantees of ``audit_trail.events`` against the real
local Postgres:

1. Row-Level Security isolates it by engagement, the same subquery-based pattern proven on every
   other write-path table in this codebase.
2. The append-only guarantee ("No UPDATE or DELETE grant exists on this table for any application
   role") is a real, enforced database permission — not just a convention the application layer
   happens to follow. This is the one test in the whole suite that proves a negative capability by
   attempting the forbidden operation directly and confirming Postgres itself refuses it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
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
    engine = create_async_engine(f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def two_engagements_with_events(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_b": str(uuid.uuid4()),
        "event_1": str(uuid.uuid4()),
        "event_2": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]

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
                {"id": ids[key], "tenant_id": ids["tenant"], "name": key},
            )
        for key in ("user_a", "user_b"):
            await conn.execute(
                text(
                    "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                    "VALUES (:id, :entra_oid, :name, :email)"
                ),
                {
                    "id": ids[key],
                    "entra_oid": f"entra-{key}-{suffix}",
                    "name": key,
                    "email": f"{key}-{suffix}@example.com",
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
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["engagement_2"], "user_id": ids["user_b"]},
        )
        for eng_key, event_key, user_key in (
            ("engagement_1", "event_1", "user_a"),
            ("engagement_2", "event_2", "user_b"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO audit_trail.events "
                    "(id, engagement_id, actor_type, actor_id, action, subject_type) "
                    "VALUES (:id, :engagement_id, 'human', :actor_id, 'test.action', 'test')"
                ),
                {"id": ids[event_key], "engagement_id": ids[eng_key], "actor_id": ids[user_key]},
            )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM audit_trail.events"))
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


async def test_app_role_sees_only_events_of_engagements_it_is_a_member_of(
    app_engine: AsyncEngine, two_engagements_with_events: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_events["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM audit_trail.events"))
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].id) == two_engagements_with_events["event_1"]


async def test_app_role_cannot_see_another_engagements_event(
    app_engine: AsyncEngine, two_engagements_with_events: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_events["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM audit_trail.events WHERE id = :id"),
            {"id": two_engagements_with_events["event_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_can_insert_an_event_for_its_own_engagement(
    app_engine: AsyncEngine, two_engagements_with_events: dict[str, str]
) -> None:
    async with app_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_events["user_a"]},
        )
        await conn.execute(
            text(
                "INSERT INTO audit_trail.events "
                "(engagement_id, actor_type, actor_id, action, subject_type) "
                "VALUES (:engagement_id, 'human', :actor_id, 'test.action', 'test')"
            ),
            {
                "engagement_id": two_engagements_with_events["engagement_1"],
                "actor_id": two_engagements_with_events["user_a"],
            },
        )


async def test_app_role_cannot_update_an_event(
    app_engine: AsyncEngine, two_engagements_with_events: dict[str, str]
) -> None:
    """The append-only guarantee proven directly: even a member of the event's own
    engagement, attempting the simplest possible UPDATE, is refused by Postgres — because
    ``auditmind_app`` was never granted UPDATE on this table at all (see the migration)."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_events["user_a"]},
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(
                text("UPDATE audit_trail.events SET action = 'tampered' WHERE id = :id"),
                {"id": two_engagements_with_events["event_1"]},
            )


async def test_app_role_cannot_delete_an_event(
    app_engine: AsyncEngine, two_engagements_with_events: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_events["user_a"]},
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(
                text("DELETE FROM audit_trail.events WHERE id = :id"),
                {"id": two_engagements_with_events["event_1"]},
            )


async def test_app_role_without_rls_context_sees_no_events(
    app_engine: AsyncEngine, two_engagements_with_events: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM audit_trail.events"))
        rows = result.all()

    assert rows == []
