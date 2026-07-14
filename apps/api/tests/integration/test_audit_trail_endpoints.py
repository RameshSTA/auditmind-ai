"""Full-stack test: confirming a finding through the real HTTP API produces a real, readable audit
event through the real HTTP API — proving the cross-context wiring (reporting → audit_trail) works
end-to-end, not just at the unit level with fakes.
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
        "member_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    member_entra_oid = f"entra-audit-member-{suffix}"
    outsider_entra_oid = f"entra-audit-outsider-{suffix}"

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
        await conn.execute(text("DELETE FROM audit_trail.events"))
        await conn.execute(text("DELETE FROM reporting.report_findings"))
        await conn.execute(text("DELETE FROM reporting.reports"))
        await conn.execute(text("DELETE FROM reporting.finding_evidence"))
        await conn.execute(text("DELETE FROM reporting.findings"))
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


def test_confirming_a_finding_produces_a_readable_audit_event(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    finding = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    client.post(
        f"/v1/engagements/{engagement_id}/findings/{finding['id']}/confirm", headers=headers
    )
    response = client.get(f"/v1/engagements/{engagement_id}/audit-events", headers=headers)

    assert response.status_code == 200
    events = response.json()
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "finding.confirmed"
    assert event["subject_type"] == "finding"
    assert event["subject_id"] == finding["id"]
    assert event["before_state"] == {"status": "draft"}
    assert event["actor_type"] == "human"


def test_outsider_cannot_read_the_audit_trail(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["outsider_entra_oid"], roles=["Auditor"]
    )

    response = client.get(
        f"/v1/engagements/{engagement_id}/audit-events",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
