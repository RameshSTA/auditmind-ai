"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the
real local Postgres, exercising the investigation lifecycle end-to-end — open, add an item,
reject a cross-engagement item, remove an item, and close with a documented conclusion. Only the
Entra JWKS network call is replaced, as in ``test_reporting_endpoints.py``.
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
    """One engagement with an Auditor member and an outsider, plus a transaction in this
    engagement and one in an unrelated second engagement — the second is what
    ``test_cannot_add_an_item_from_a_different_engagement`` exercises the cross-engagement
    subject check against, over real HTTP rather than just fakes."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "other_engagement": str(uuid.uuid4()),
        "auditor_user": str(uuid.uuid4()),
        "transaction": str(uuid.uuid4()),
        "other_transaction": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    oids = {
        "auditor_user": f"entra-auditor-{suffix}",
        "outsider": f"entra-outsider-{suffix}",
    }

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
                "VALUES (:id, :entra_oid, 'Auditor User', :email)"
            ),
            {
                "id": ids["auditor_user"],
                "entra_oid": oids["auditor_user"],
                "email": f"auditor-{suffix}@example.com",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["engagement"], "user_id": ids["auditor_user"]},
        )
        # The auditor is also a member of the second, unrelated engagement — needed to exercise
        # the cross-engagement subject check (both engagements are individually authorized reads
        # for this user; only the application-level check in InvestigationService should reject
        # mixing them). Without this second membership, RLS alone would already hide
        # `other_transaction` and the request would 404 rather than exercise the 422 path this
        # test is actually about — the same setup `test_reporting_endpoints.py`'s
        # cross-engagement evidence test uses for `ChunkLookup`.
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["other_engagement"], "user_id": ids["auditor_user"]},
        )
        for eng_key, txn_key in (
            ("engagement", "transaction"),
            ("other_engagement", "other_transaction"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO risk.transactions "
                    "(id, engagement_id, source_system, amount, currency, transaction_date, "
                    " raw_payload, created_by) "
                    "VALUES (:id, :engagement_id, 'manual_import', 100.00, 'USD', "
                    "        current_date, '{}'::jsonb, :created_by)"
                ),
                {
                    "id": ids[txn_key],
                    "engagement_id": ids[eng_key],
                    "created_by": ids["auditor_user"],
                },
            )

    ids.update(oids)
    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM audit_trail.events"))
        await conn.execute(text("DELETE FROM investigations.investigation_items"))
        await conn.execute(text("DELETE FROM investigations.investigations"))
        await conn.execute(text("DELETE FROM risk.transactions"))
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE entra_object_id IN (:a, :b)"),
            {"a": oids["auditor_user"], "b": oids["outsider"]},
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


def _auth_header(rsa_keypair: KeyMaterial, subject: str) -> dict[str, str]:
    token = make_token(rsa_keypair, subject=subject, roles=["Auditor"])
    return {"Authorization": f"Bearer {token}"}


def test_auditor_can_open_an_investigation_and_it_starts_open(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "Vendor XYZ round-dollar pattern", "description": "Multiple flags."},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["title"] == "Vendor XYZ round-dollar pattern"


def test_outsider_cannot_open_an_investigation(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["outsider"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "t", "description": "d"},
    )

    assert response.status_code == 403


def test_can_add_an_item_referencing_a_real_transaction_in_the_same_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    investigation_id = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "t", "description": "d"},
    ).json()["id"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
        headers=headers,
        json={
            "subject_type": "transaction",
            "subject_id": seeded_engagement["transaction"],
            "note": "Looks anomalous",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["subject_type"] == "transaction"
    assert body["subject_id"] == seeded_engagement["transaction"]

    items = client.get(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
        headers=headers,
    )
    assert items.status_code == 200
    assert len(items.json()) == 1


def test_cannot_add_an_item_from_a_different_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """The exact protection ``InvestigationSubjectLookup`` exists for: a real transaction, just
    not one belonging to this investigation's engagement."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    investigation_id = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "t", "description": "d"},
    ).json()["id"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
        headers=headers,
        json={"subject_type": "transaction", "subject_id": seeded_engagement["other_transaction"]},
    )

    assert response.status_code == 422


def test_close_requires_a_conclusion(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    investigation_id = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "t", "description": "d"},
    ).json()["id"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/close",
        headers=headers,
        json={"conclusion": ""},
    )

    assert response.status_code == 422


def test_close_with_a_conclusion_marks_investigation_closed_and_blocks_further_items(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    investigation_id = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "t", "description": "d"},
    ).json()["id"]

    close_response = client.post(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/close",
        headers=headers,
        json={"conclusion": "Confirmed as a legitimate recurring vendor payment."},
    )
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "closed"

    add_after_close = client.post(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
        headers=headers,
        json={"subject_type": "transaction", "subject_id": seeded_engagement["transaction"]},
    )
    assert add_after_close.status_code == 409


def test_remove_an_item_deletes_it_from_the_working_set(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    investigation_id = client.post(
        f"/v1/engagements/{engagement_id}/investigations",
        headers=headers,
        json={"title": "t", "description": "d"},
    ).json()["id"]
    item_id = client.post(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
        headers=headers,
        json={"subject_type": "transaction", "subject_id": seeded_engagement["transaction"]},
    ).json()["id"]

    delete_response = client.delete(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items/{item_id}",
        headers=headers,
    )
    assert delete_response.status_code == 204

    items = client.get(
        f"/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
        headers=headers,
    )
    assert items.json() == []
