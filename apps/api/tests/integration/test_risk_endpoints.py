"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the
real local Postgres, exercising transaction import → rule-engine scan → anomaly disposition
(Phase 7 §1-§2). Only the Entra JWKS network call is replaced, as in the other endpoint test
files.
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
        "auditor_user": str(uuid.uuid4()),
        "compliance_manager_user": str(uuid.uuid4()),
        "cae_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    oids = {
        "auditor_user": f"entra-risk-auditor-{suffix}",
        "compliance_manager_user": f"entra-risk-compliance-{suffix}",
        "cae_user": f"entra-risk-cae-{suffix}",
        "outsider": f"entra-risk-outsider-{suffix}",
    }

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
        for key in ("auditor_user", "compliance_manager_user", "cae_user"):
            await conn.execute(
                text(
                    "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                    "VALUES (:id, :entra_oid, :name, :email)"
                ),
                {
                    "id": ids[key],
                    "entra_oid": oids[key],
                    "name": key,
                    "email": f"{key}-{suffix}@example.com",
                },
            )
        for key, role in (
            ("auditor_user", "Auditor"),
            ("compliance_manager_user", "ComplianceManager"),
            ("cae_user", "CAE"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                    "VALUES (:engagement_id, :user_id, :role)"
                ),
                {"engagement_id": ids["engagement"], "user_id": ids[key], "role": role},
            )

    ids.update(oids)
    yield ids

    async with admin_engine.begin() as conn:
        # audit_trail.events has an FK into identity.engagements — must be cleared before the
        # engagement itself, or that DELETE fails with a foreign-key violation (disposition now
        # writes an audit event per Increment 06).
        await conn.execute(text("DELETE FROM audit_trail.events"))
        await conn.execute(text("DELETE FROM risk.anomalies"))
        await conn.execute(text("DELETE FROM risk.transactions"))
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE entra_object_id IN (:a, :b, :c, :d)"),
            {
                "a": oids["auditor_user"],
                "b": oids["compliance_manager_user"],
                "c": oids["cae_user"],
                "d": oids["outsider"],
            },
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


def _auth_header(rsa_keypair: KeyMaterial, subject: str) -> dict[str, str]:
    token = make_token(rsa_keypair, subject=subject, roles=["Auditor"])
    return {"Authorization": f"Bearer {token}"}


def test_auditor_can_import_transactions(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {
                    "amount": "5000.00",
                    "currency": "USD",
                    "transaction_date": "2026-01-01",
                    "vendor_name": "Acme Corp",
                }
            ]
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body) == 1
    assert body[0]["amount"] == "5000.00"


def test_auditor_can_import_multiple_transactions_in_one_call(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """Regression test: a single import call with 2+ records batches into one multi-row
    INSERT...RETURNING. The domain layer generates transaction ids as strings while the ORM
    column is UUID(as_uuid=True) — passing the string straight through broke SQLAlchemy's
    insertmanyvalues sentinel matching (``InvalidRequestError: Can't match sentinel values...``)
    the first time this endpoint was ever exercised with more than one record at once, since every
    prior test (including the one above) only ever imported a single row per call."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {
                    "amount": "9800.00",
                    "currency": "USD",
                    "transaction_date": "2026-01-15",
                    "vendor_name": "Acme Supplies",
                },
                {
                    "amount": "9800.00",
                    "currency": "USD",
                    "transaction_date": "2026-01-16",
                    "vendor_name": "Acme Supplies",
                },
            ]
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body) == 2
    assert {row["amount"] for row in body} == {"9800.00"}
    assert len({row["id"] for row in body}) == 2


def test_cae_cannot_import_transactions(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["cae_user"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {"amount": "100.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"}
            ]
        },
    )

    assert response.status_code == 403


def test_scan_detects_a_round_dollar_anomaly_end_to_end(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {"amount": "7000.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"}
            ]
        },
    )

    response = client.post(f"/v1/engagements/{engagement_id}/risk/scan", headers=headers)

    assert response.status_code == 201
    anomalies = response.json()
    assert any(a["anomaly_type"] == "round_dollar" for a in anomalies)

    listed = client.get(f"/v1/engagements/{engagement_id}/anomalies", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == len(anomalies)


def test_scan_persists_multiple_anomalies_from_one_scan(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """Regression test for the same class of bug as
    ``test_auditor_can_import_multiple_transactions_in_one_call``, on the anomaly repository's
    ``bulk_create`` instead of the transaction one: two round-dollar transactions in one scan
    produce two anomalies persisted via a single multi-row INSERT...RETURNING."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {"amount": "7000.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"},
                {"amount": "8000.00", "transaction_date": "2026-01-02", "vendor_name": "Acme"},
            ]
        },
    )

    response = client.post(f"/v1/engagements/{engagement_id}/risk/scan", headers=headers)

    assert response.status_code == 201
    round_dollar = [a for a in response.json() if a["anomaly_type"] == "round_dollar"]
    assert len(round_dollar) == 2
    assert len({a["id"] for a in round_dollar}) == 2


def test_scan_is_idempotent_over_http(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {"amount": "8000.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"}
            ]
        },
    )
    first_scan = client.post(f"/v1/engagements/{engagement_id}/risk/scan", headers=headers)

    second_scan = client.post(f"/v1/engagements/{engagement_id}/risk/scan", headers=headers)

    assert len(first_scan.json()) >= 1
    assert second_scan.json() == []


def test_auditor_can_disposition_an_anomaly(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {"amount": "9000.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"}
            ]
        },
    )
    anomaly_id = client.post(
        f"/v1/engagements/{engagement_id}/risk/scan", headers=headers
    ).json()[0]["id"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/anomalies/{anomaly_id}/disposition",
        headers=headers,
        json={"status": "true_positive"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "true_positive"


def test_compliance_manager_cannot_disposition_an_anomaly(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """Per Phase 11 §2's RBAC matrix, the HITL disposition row is Auditor/Fraud Analyst only —
    the same restriction Increment 04 applied to confirming/rejecting a finding."""
    engagement_id = seeded_engagement["engagement"]
    auditor_headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    compliance_headers = _auth_header(
        rsa_keypair, seeded_engagement["compliance_manager_user"]
    )
    client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=auditor_headers,
        json={
            "transactions": [
                {"amount": "6000.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"}
            ]
        },
    )
    anomaly_id = client.post(
        f"/v1/engagements/{engagement_id}/risk/scan", headers=auditor_headers
    ).json()[0]["id"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/anomalies/{anomaly_id}/disposition",
        headers=compliance_headers,
        json={"status": "true_positive"},
    )

    assert response.status_code == 403


def test_dispositioning_an_already_dispositioned_anomaly_is_rejected(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {"amount": "4000.00", "transaction_date": "2026-01-01", "vendor_name": "Acme"}
            ]
        },
    )
    anomaly_id = client.post(
        f"/v1/engagements/{engagement_id}/risk/scan", headers=headers
    ).json()[0]["id"]
    client.post(
        f"/v1/engagements/{engagement_id}/anomalies/{anomaly_id}/disposition",
        headers=headers,
        json={"status": "true_positive"},
    )

    response = client.post(
        f"/v1/engagements/{engagement_id}/anomalies/{anomaly_id}/disposition",
        headers=headers,
        json={"status": "false_positive"},
    )

    assert response.status_code == 409
