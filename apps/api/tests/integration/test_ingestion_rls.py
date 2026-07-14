"""Integration tests proving the ingestion schema's Row-Level Security policies (Phase 4 §12)
actually isolate engagements at the database level.

Unlike Increment 02's ``identity.engagement_members`` policy (a direct session-variable equality
check), these policies derive authorization via a subquery against ``identity.engagement_members``
— a user may see a document/chunk row if and only if they are *currently* a member of its
``engagement_id``. This file proves that subquery-based approach holds under the same attack
shape Increment 02 already proved against the simpler policy.
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
    engine = create_async_engine(f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def two_engagements_with_documents(
    admin_engine: AsyncEngine,
) -> AsyncIterator[dict[str, str]]:
    """User A is a member of engagement 1 only, which has one document with one chunk; user B is
    a member of engagement 2 only, same shape. Neither is a member of the other's engagement."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_b": str(uuid.uuid4()),
        "document_1": str(uuid.uuid4()),
        "document_2": str(uuid.uuid4()),
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
        for eng_key, doc_key, user_key in (
            ("engagement_1", "document_1", "user_a"),
            ("engagement_2", "document_2", "user_b"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO ingestion.documents "
                    "(id, engagement_id, original_filename, storage_uri, sha256_hash, "
                    " mime_type, status, ingested_by) "
                    "VALUES (:id, :engagement_id, 'f.txt', 'uri', 'hash', 'text/plain', "
                    "        'parsed', :ingested_by)"
                ),
                {
                    "id": ids[doc_key],
                    "engagement_id": ids[eng_key],
                    "ingested_by": ids[user_key],
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO ingestion.chunks "
                    "(id, document_id, engagement_id, chunk_index, text, char_start, char_end) "
                    "VALUES (:id, :document_id, :engagement_id, 0, 'chunk text', 0, 10)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "document_id": ids[doc_key],
                    "engagement_id": ids[eng_key],
                },
            )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM ingestion.chunks"))
        await conn.execute(text("DELETE FROM ingestion.documents"))
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


async def test_admin_can_see_both_engagements_documents(
    admin_engine: AsyncEngine, two_engagements_with_documents: dict[str, str]
) -> None:
    """Sanity check on the fixture: both documents genuinely exist before proving isolation."""
    async with admin_engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM ingestion.documents"))
        assert result.scalar_one() == 2


async def test_app_role_sees_only_documents_of_engagements_it_is_a_member_of(
    app_engine: AsyncEngine, two_engagements_with_documents: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_documents["user_a"]},
        )
        result = await conn.execute(text("SELECT id, engagement_id FROM ingestion.documents"))
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].id) == two_engagements_with_documents["document_1"]


async def test_app_role_cannot_see_another_engagements_document_even_queried_by_id(
    app_engine: AsyncEngine, two_engagements_with_documents: dict[str, str]
) -> None:
    """The harder case: explicitly asking for user B's engagement's document by its exact id,
    while RLS context is set to user A, still returns nothing — the subquery against
    engagement_members is evaluated regardless of how the row was looked up."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_documents["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM ingestion.documents WHERE id = :doc_id"),
            {"doc_id": two_engagements_with_documents["document_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_cannot_see_chunks_of_a_document_it_has_no_membership_for(
    app_engine: AsyncEngine, two_engagements_with_documents: dict[str, str]
) -> None:
    """Proves isolation holds on ``chunks`` independently of ``documents`` — this is the exact
    scenario the ``list_document_chunks`` endpoint relies on RLS to protect (main.py's docstring
    on that route)."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_documents["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM ingestion.chunks WHERE document_id = :doc_id"),
            {"doc_id": two_engagements_with_documents["document_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_without_rls_context_sees_no_documents(
    app_engine: AsyncEngine, two_engagements_with_documents: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM ingestion.documents"))
        rows = result.all()

    assert rows == []
