"""Integration tests proving the risk schema's Row-Level Security policies actually isolate
engagements at the database level — the same subquery-based pattern already established
elsewhere, proven again on ``risk.transactions``/``risk.anomalies``/``risk.risk_scores``."""

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
    engine = create_async_engine(f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def two_engagements_with_transactions(
    admin_engine: AsyncEngine,
) -> AsyncIterator[dict[str, str]]:
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_b": str(uuid.uuid4()),
        "transaction_1": str(uuid.uuid4()),
        "transaction_2": str(uuid.uuid4()),
        "anomaly_1": str(uuid.uuid4()),
        "anomaly_2": str(uuid.uuid4()),
        "risk_score_1": str(uuid.uuid4()),
        "risk_score_2": str(uuid.uuid4()),
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
        rows = (
            ("engagement_1", "transaction_1", "anomaly_1", "user_a"),
            ("engagement_2", "transaction_2", "anomaly_2", "user_b"),
        )
        for eng_key, txn_key, anomaly_key, user_key in rows:
            await conn.execute(
                text(
                    "INSERT INTO risk.transactions "
                    "(id, engagement_id, source_system, amount, currency, transaction_date, "
                    " raw_payload, created_by) "
                    "VALUES (:id, :engagement_id, 'test', 500.00, 'USD', '2026-01-01', "
                    "        '{}'::jsonb, :created_by)"
                ),
                {"id": ids[txn_key], "engagement_id": ids[eng_key], "created_by": ids[user_key]},
            )
            await conn.execute(
                text(
                    "INSERT INTO risk.anomalies "
                    "(id, engagement_id, transaction_id, anomaly_type, severity, status) "
                    "VALUES (:id, :engagement_id, :transaction_id, 'round_dollar', 'low', 'open')"
                ),
                {
                    "id": ids[anomaly_key],
                    "engagement_id": ids[eng_key],
                    "transaction_id": ids[txn_key],
                },
            )
        for eng_key, txn_key, score_key in (
            ("engagement_1", "transaction_1", "risk_score_1"),
            ("engagement_2", "transaction_2", "risk_score_2"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO risk.risk_scores "
                    "(id, engagement_id, subject_type, subject_id, score, score_version) "
                    "VALUES (:id, :engagement_id, 'transaction', :subject_id, 50.00, 'v1')"
                ),
                {"id": ids[score_key], "engagement_id": ids[eng_key], "subject_id": ids[txn_key]},
            )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM risk.risk_scores"))
        await conn.execute(text("DELETE FROM risk.anomalies"))
        await conn.execute(text("DELETE FROM risk.transactions"))
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


async def test_admin_can_see_both_engagements_transactions(
    admin_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with admin_engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM risk.transactions"))
        assert result.scalar_one() == 2


async def test_app_role_sees_only_transactions_of_engagements_it_is_a_member_of(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_transactions["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM risk.transactions"))
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].id) == two_engagements_with_transactions["transaction_1"]


async def test_app_role_cannot_see_another_engagements_transaction_even_queried_by_id(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_transactions["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM risk.transactions WHERE id = :id"),
            {"id": two_engagements_with_transactions["transaction_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_cannot_see_another_engagements_anomaly(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_transactions["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM risk.anomalies WHERE id = :id"),
            {"id": two_engagements_with_transactions["anomaly_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_without_rls_context_sees_no_transactions(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM risk.transactions"))
        rows = result.all()

    assert rows == []


async def test_app_role_sees_only_risk_scores_of_engagements_it_is_a_member_of(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_transactions["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM risk.risk_scores"))
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].id) == two_engagements_with_transactions["risk_score_1"]


async def test_app_role_cannot_see_another_engagements_risk_score_even_queried_by_id(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_transactions["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM risk.risk_scores WHERE id = :id"),
            {"id": two_engagements_with_transactions["risk_score_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_without_rls_context_sees_no_risk_scores(
    app_engine: AsyncEngine, two_engagements_with_transactions: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM risk.risk_scores"))
        rows = result.all()

    assert rows == []
