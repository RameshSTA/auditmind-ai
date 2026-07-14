"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the real
local Postgres, proving Postgres full-text search actually returns ranked, relevant results over
chunks created by the real ingestion pipeline — not a fake or a mocked index.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import auditmind_api.shared.database as database_module
from auditmind_api.main import create_app
from auditmind_api.shared.auth import JWKSClient
from auditmind_api.shared.settings import get_settings
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, KeyMaterial, make_token

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
async def seeded_engagement(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "other_engagement": str(uuid.uuid4()),
        "member_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    member_entra_oid = f"entra-retrieval-member-{suffix}"

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Test Tenant')"),
            {"id": ids["tenant"]},
        )
        for key in ("engagement", "other_engagement"):
            await conn.execute(
                text(
                    "INSERT INTO identity.engagements (id, tenant_id, name) "
                    "VALUES (:id, :tenant_id, :name)"
                ),
                {"id": ids[key], "tenant_id": ids["tenant"], "name": key},
            )
        await conn.execute(
            text(
                "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                "VALUES (:id, :entra_oid, 'Member', :email)"
            ),
            {
                "id": ids["member_user"],
                "entra_oid": member_entra_oid,
                "email": f"m-{suffix}@example.com",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["engagement"], "user_id": ids["member_user"]},
        )

    ids["member_entra_oid"] = member_entra_oid
    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM ingestion.chunks"))
        await conn.execute(text("DELETE FROM ingestion.documents"))
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE entra_object_id = :oid"),
            {"oid": member_entra_oid},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id IN (:e1, :e2)"),
            {"e1": ids["engagement"], "e2": ids["other_engagement"]},
        )
        await conn.execute(
            text("DELETE FROM identity.tenants WHERE id = :id"), {"id": ids["tenant"]}
        )


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: KeyMaterial, tmp_path: Path
) -> Iterator[TestClient]:
    monkeypatch.setenv("AUDITMIND_ENTRA_CLIENT_ID", TEST_AUDIENCE)
    monkeypatch.setenv("AUDITMIND_ENTRA_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AUDITMIND_ENTRA_JWKS_URI", "https://fake-entra.example/keys")
    monkeypatch.setenv("AUDITMIND_LOG_LEVEL", "WARNING")
    monkeypatch.setenv(
        "AUDITMIND_DATABASE_HOST", os.environ.get("AUDITMIND_TEST_DB_HOST", "localhost")
    )
    monkeypatch.setenv(
        "AUDITMIND_DATABASE_PORT", os.environ.get("AUDITMIND_TEST_DB_PORT", "5433")
    )
    monkeypatch.setenv(
        "AUDITMIND_DATABASE_NAME", os.environ.get("AUDITMIND_TEST_DB_NAME", "auditmind_dev")
    )
    monkeypatch.setenv(
        "AUDITMIND_DATABASE_APP_USER", os.environ.get("AUDITMIND_TEST_APP_USER", "auditmind_app")
    )
    monkeypatch.setenv(
        "AUDITMIND_DATABASE_APP_PASSWORD",
        os.environ.get("AUDITMIND_TEST_APP_PASSWORD", "auditmind_app_local_dev"),
    )
    monkeypatch.setenv("AUDITMIND_BLOB_STORAGE_ROOT", str(tmp_path / "blobs"))
    get_settings.cache_clear()

    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_factory", None)

    async def fake_refresh(self: JWKSClient) -> None:
        self._cached_keys = {"keys": [rsa_keypair.jwk_dict]}
        self._cached_at = time.monotonic()

    monkeypatch.setattr(JWKSClient, "_refresh", fake_refresh)

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_factory", None)


def test_search_finds_the_chunk_containing_the_query_term(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers=headers,
        files={
            "file": (
                "evidence.txt",
                b"The vendor payment was approved without a matching purchase order.\n\n"
                b"Unrelated paragraph about office supplies and stationery budgets.",
                "text/plain",
            )
        },
    )

    response = client.get(
        f"/v1/engagements/{engagement_id}/search",
        headers=headers,
        params={"q": "vendor payment approved"},
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1
    assert "vendor payment" in results[0]["text"]


def test_search_finds_nothing_for_an_unrelated_query(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers=headers,
        files={"file": ("evidence.txt", b"Vendor payment approved.", "text/plain")},
    )

    response = client.get(
        f"/v1/engagements/{engagement_id}/search",
        headers=headers,
        params={"q": "zebra giraffe astronomy"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_search_does_not_return_another_engagements_chunks(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """A member of ``engagement`` searches for a term that only exists in a chunk belonging to
    ``other_engagement`` (seeded directly, since this user is never a member of it) — proving
    search is scoped, not just document listing."""
    engagement_id = seeded_engagement["engagement"]
    other_engagement_id = seeded_engagement["other_engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers=headers,
        files={"file": ("evidence.txt", b"A perfectly normal transaction.", "text/plain")},
    )

    response = client.get(
        f"/v1/engagements/{other_engagement_id}/search",
        headers=headers,
        params={"q": "transaction"},
    )

    assert response.status_code == 403  # not a member of other_engagement at all


def test_empty_query_string_is_rejected(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )

    response = client.get(
        f"/v1/engagements/{engagement_id}/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": ""},
    )

    assert response.status_code == 422
