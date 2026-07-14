"""Integration tests proving the reporting schema's Row-Level Security policies actually isolate
engagements at the database level, across all four tables in the schema.

Same subquery-based pattern as the ``ingestion`` schema, plus one new shape:
``reporting.report_findings`` has no ``engagement_id`` column of its own, so its policy joins
through ``reporting.reports`` instead — this file proves that join-based variant holds under the
same attack shape the direct-column policies are already proven against.
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
async def two_engagements_with_findings(
    admin_engine: AsyncEngine,
) -> AsyncIterator[dict[str, str]]:
    """User A is a member of engagement 1 only, which has a document/chunk, a finding, a citation
    on that finding, a confirmed report, and a report_findings row. User B has the identical shape
    under engagement 2. Neither is a member of the other's engagement."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "user_a": str(uuid.uuid4()),
        "user_b": str(uuid.uuid4()),
        "document_1": str(uuid.uuid4()),
        "document_2": str(uuid.uuid4()),
        "chunk_1": str(uuid.uuid4()),
        "chunk_2": str(uuid.uuid4()),
        "finding_1": str(uuid.uuid4()),
        "finding_2": str(uuid.uuid4()),
        "evidence_1": str(uuid.uuid4()),
        "evidence_2": str(uuid.uuid4()),
        "report_1": str(uuid.uuid4()),
        "report_2": str(uuid.uuid4()),
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
            (
                "engagement_1",
                "document_1",
                "chunk_1",
                "finding_1",
                "evidence_1",
                "report_1",
                "user_a",
            ),
            (
                "engagement_2",
                "document_2",
                "chunk_2",
                "finding_2",
                "evidence_2",
                "report_2",
                "user_b",
            ),
        )
        for eng_key, doc_key, chunk_key, finding_key, evidence_key, report_key, user_key in rows:
            await conn.execute(
                text(
                    "INSERT INTO ingestion.documents "
                    "(id, engagement_id, original_filename, storage_uri, sha256_hash, "
                    " mime_type, status, ingested_by) "
                    "VALUES (:id, :engagement_id, 'f.txt', 'uri', 'hash', 'text/plain', "
                    "        'parsed', :ingested_by)"
                ),
                {"id": ids[doc_key], "engagement_id": ids[eng_key], "ingested_by": ids[user_key]},
            )
            await conn.execute(
                text(
                    "INSERT INTO ingestion.chunks "
                    "(id, document_id, engagement_id, chunk_index, text, char_start, char_end) "
                    "VALUES (:id, :document_id, :engagement_id, 0, 'chunk text', 0, 10)"
                ),
                {"id": ids[chunk_key], "document_id": ids[doc_key], "engagement_id": ids[eng_key]},
            )
            await conn.execute(
                text(
                    "INSERT INTO reporting.findings "
                    "(id, engagement_id, title, description, severity, status, created_by) "
                    "VALUES (:id, :engagement_id, 'Finding', 'Description', 'high', 'draft', "
                    "        :created_by)"
                ),
                {
                    "id": ids[finding_key],
                    "engagement_id": ids[eng_key],
                    "created_by": ids[user_key],
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO reporting.finding_evidence "
                    "(id, finding_id, engagement_id, chunk_id, citation_text) "
                    "VALUES (:id, :finding_id, :engagement_id, :chunk_id, 'see chunk')"
                ),
                {
                    "id": ids[evidence_key],
                    "finding_id": ids[finding_key],
                    "engagement_id": ids[eng_key],
                    "chunk_id": ids[chunk_key],
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO reporting.reports "
                    "(id, engagement_id, version, generated_by, body_markdown) "
                    "VALUES (:id, :engagement_id, 1, :generated_by, 'report body')"
                ),
                {
                    "id": ids[report_key],
                    "engagement_id": ids[eng_key],
                    "generated_by": ids[user_key],
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO reporting.report_findings (report_id, finding_id) "
                    "VALUES (:report_id, :finding_id)"
                ),
                {"report_id": ids[report_key], "finding_id": ids[finding_key]},
            )

    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM reporting.report_findings"))
        await conn.execute(text("DELETE FROM reporting.reports"))
        await conn.execute(text("DELETE FROM reporting.finding_evidence"))
        await conn.execute(text("DELETE FROM reporting.findings"))
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


async def test_admin_can_see_both_engagements_findings(
    admin_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    """Sanity check on the fixture: both findings genuinely exist before proving isolation."""
    async with admin_engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM reporting.findings"))
        assert result.scalar_one() == 2


async def test_app_role_sees_only_findings_of_engagements_it_is_a_member_of(
    app_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_findings["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM reporting.findings"))
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].id) == two_engagements_with_findings["finding_1"]


async def test_app_role_cannot_see_another_engagements_finding_even_queried_by_id(
    app_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_findings["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM reporting.findings WHERE id = :finding_id"),
            {"finding_id": two_engagements_with_findings["finding_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_cannot_see_finding_evidence_of_a_finding_it_has_no_membership_for(
    app_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_findings["user_a"]},
        )
        result = await conn.execute(
            text("SELECT * FROM reporting.finding_evidence WHERE finding_id = :finding_id"),
            {"finding_id": two_engagements_with_findings["finding_2"]},
        )
        rows = result.all()

    assert rows == []


async def test_app_role_cannot_see_another_engagements_report(
    app_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_findings["user_a"]},
        )
        result = await conn.execute(text("SELECT id FROM reporting.reports"))
        rows = result.all()

    assert len(rows) == 1
    assert str(rows[0].id) == two_engagements_with_findings["report_1"]


async def test_app_role_cannot_see_report_findings_via_the_joined_policy(
    app_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    """The one policy in this migration that isn't a direct-column check — proves the join through
    ``reporting.reports`` enforces isolation just as strictly as the other three tables' direct
    ``engagement_id`` comparison."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": two_engagements_with_findings["user_a"]},
        )
        own = await conn.execute(
            text("SELECT * FROM reporting.report_findings WHERE report_id = :report_id"),
            {"report_id": two_engagements_with_findings["report_1"]},
        )
        others = await conn.execute(
            text("SELECT * FROM reporting.report_findings WHERE report_id = :report_id"),
            {"report_id": two_engagements_with_findings["report_2"]},
        )

    assert len(own.all()) == 1
    assert others.all() == []


async def test_app_role_without_rls_context_sees_no_findings(
    app_engine: AsyncEngine, two_engagements_with_findings: dict[str, str]
) -> None:
    async with app_engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM reporting.findings"))
        rows = result.all()

    assert rows == []
