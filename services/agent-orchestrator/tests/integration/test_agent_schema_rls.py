"""Integration tests proving the `agent` schema's Row-Level Security policies (Phase 4 §12) — the
same subquery-based pattern every prior increment's RLS proof uses, applied to `agent.runs` and
`agent.hitl_interrupts` (Increment 12)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGENT_MIGRATION_DATABASE_URL"),
    reason="Requires a real Postgres instance — set AGENT_MIGRATION_DATABASE_URL to run.",
)


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["AGENT_MIGRATION_DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    host = os.environ.get("AGENT_TEST_DB_HOST", "localhost")
    port = os.environ.get("AGENT_TEST_DB_PORT", "5433")
    name = os.environ.get("AGENT_TEST_DB_NAME", "auditmind")
    user = os.environ.get("AGENT_TEST_APP_USER", "auditmind_app")
    password = os.environ.get("AGENT_TEST_APP_PASSWORD", "auditmind_app_local_dev_only")
    engine = create_async_engine(f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def two_engagements_with_runs(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_b": str(uuid.uuid4()),
        "run_1": str(uuid.uuid4()),
        "run_2": str(uuid.uuid4()),
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
        for key, engagement_key, user_key in (
            ("run_1", "engagement_1", "user_a"),
            ("run_2", "engagement_2", "user_b"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO agent.runs (id, engagement_id, use_case, status, "
                    "initiated_by, task) "
                    "VALUES (:id, :engagement_id, 'control_test', 'pending', :user_id, 'task')"
                ),
                {"id": ids[key], "engagement_id": ids[engagement_key], "user_id": ids[user_key]},
            )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM agent.hitl_interrupts WHERE run_id IN (:r1, :r2)"),
            {"r1": ids["run_1"], "r2": ids["run_2"]},
        )
        await conn.execute(
            text("DELETE FROM agent.runs WHERE id IN (:r1, :r2)"),
            {"r1": ids["run_1"], "r2": ids["run_2"]},
        )
        await conn.execute(
            text("DELETE FROM identity.engagement_members WHERE engagement_id IN (:e1, :e2)"),
            {"e1": ids["engagement_1"], "e2": ids["engagement_2"]},
        )
        await conn.execute(
            text("DELETE FROM identity.users WHERE id IN (:u1, :u2)"),
            {"u1": ids["user_a"], "u2": ids["user_b"]},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id IN (:e1, :e2)"),
            {"e1": ids["engagement_1"], "e2": ids["engagement_2"]},
        )
        await conn.execute(text("DELETE FROM identity.tenants WHERE id = :t"), {"t": ids["tenant"]})


async def test_a_member_only_sees_their_own_engagements_run(
    app_engine: AsyncEngine, two_engagements_with_runs: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, false)"),
            {"uid": two_engagements_with_runs["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM agent.runs"))
        visible_ids = {str(row[0]) for row in result.fetchall()}

    assert visible_ids == {two_engagements_with_runs["run_1"]}


async def test_a_non_member_sees_no_runs_at_all(
    app_engine: AsyncEngine, two_engagements_with_runs: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, false)"),
            {"uid": str(uuid.uuid4())},  # a user id that is nobody's member row
        )
        result = await conn.execute(text("SELECT id FROM agent.runs"))
        visible_ids = {str(row[0]) for row in result.fetchall()}

    assert visible_ids == set()


async def test_hitl_interrupts_are_isolated_by_the_same_engagement_membership(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    two_engagements_with_runs: dict[str, str],
) -> None:
    interrupt_1 = str(uuid.uuid4())
    interrupt_2 = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent.hitl_interrupts (id, run_id, engagement_id, step_name) "
                "VALUES (:id, :run_id, :engagement_id, 'hitl')"
            ),
            {
                "id": interrupt_1,
                "run_id": two_engagements_with_runs["run_1"],
                "engagement_id": two_engagements_with_runs["engagement_1"],
            },
        )
        await conn.execute(
            text(
                "INSERT INTO agent.hitl_interrupts (id, run_id, engagement_id, step_name) "
                "VALUES (:id, :run_id, :engagement_id, 'hitl')"
            ),
            {
                "id": interrupt_2,
                "run_id": two_engagements_with_runs["run_2"],
                "engagement_id": two_engagements_with_runs["engagement_2"],
            },
        )

    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, false)"),
            {"uid": two_engagements_with_runs["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM agent.hitl_interrupts"))
        visible_ids = {str(row[0]) for row in result.fetchall()}

    assert visible_ids == {interrupt_1}
