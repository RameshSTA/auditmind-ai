"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the
real local Postgres, exercising the complete upload → dedup-check → store → parse → chunk →
persist chain (Phase 1 UC-01, Phase 6 §1) — only the Entra JWKS network call is replaced, as in
``test_identity_endpoints.py``.
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
    """One engagement with one member (the "auditor" persona) — a second Entra identity in the
    tests below is never granted membership, to exercise the 403 path."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "member_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    member_entra_oid = f"entra-ingestion-member-{suffix}"
    outsider_entra_oid = f"entra-ingestion-outsider-{suffix}"

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Test Tenant')"),
            {"id": ids["tenant"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagements (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'Eng')"
            ),
            {"id": ids["engagement"], "tenant_id": ids["tenant"]},
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
    ids["outsider_entra_oid"] = outsider_entra_oid
    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM ingestion.chunks"))
        await conn.execute(text("DELETE FROM ingestion.documents"))
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE entra_object_id IN (:a, :b)"),
            {"a": member_entra_oid, "b": outsider_entra_oid},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id = :id"), {"id": ids["engagement"]}
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
    # A real temp directory per test, never the repo's ./data/blobs default — isolated and
    # auto-cleaned by pytest's tmp_path, consistent with never polluting the working tree.
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


def test_member_can_upload_a_document_and_it_is_parsed_and_chunked(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    engagement_id = seeded_engagement["engagement"]
    content = b"Paragraph one of the evidence.\n\nParagraph two of the evidence."

    response = client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("evidence.txt", content, "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "parsed"
    assert body["original_filename"] == "evidence.txt"
    document_id = body["id"]

    chunks_response = client.get(
        f"/v1/engagements/{engagement_id}/documents/{document_id}/chunks",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chunks_response.status_code == 200
    chunks = chunks_response.json()
    assert len(chunks) >= 1
    assert "Paragraph one" in " ".join(c["text"] for c in chunks)

    documents_response = client.get(
        f"/v1/engagements/{engagement_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert documents_response.status_code == 200
    assert any(d["id"] == document_id for d in documents_response.json())


def test_uploading_the_same_content_twice_returns_the_same_document(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    engagement_id = seeded_engagement["engagement"]
    content = b"Identical content uploaded twice."

    first = client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("first.txt", content, "text/plain")},
    )
    second = client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("second-name.txt", content, "text/plain")},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_non_member_cannot_upload_to_someone_elses_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    token = make_token(
        rsa_keypair, subject=seeded_engagement["outsider_entra_oid"], roles=["Auditor"]
    )
    engagement_id = seeded_engagement["engagement"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("evidence.txt", b"content", "text/plain")},
    )

    assert response.status_code == 403


def test_uploading_an_unsupported_mime_type_marks_the_document_failed(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """The upload itself succeeds (the file is stored) but parsing fails — proving the
    "stored-but-failed" path (IngestionService's design) end-to-end over real HTTP, not just at
    the unit level."""
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    engagement_id = seeded_engagement["engagement"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("image.png", b"\x89PNG fake bytes", "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["type"] == "https://auditmind.ai/errors/unsupported-mime-type"
