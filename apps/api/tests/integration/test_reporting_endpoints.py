"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the
real local Postgres, exercising the finding lifecycle end-to-end — create, cite evidence,
confirm/reject (the mandatory human sign-off gate), and generate a report. Only the Entra JWKS
network call is replaced, as in ``test_ingestion_endpoints.py``.
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
    """One engagement with three members of different roles (Auditor, FraudAnalyst,
    ComplianceManager — the three permitted to author findings per the RBAC matrix) plus
    a non-member outsider, a chunk to cite as evidence, and a second engagement with its own chunk
    the ``auditor`` user is *also* a member of — the setup ``test_..._cross_engagement_evidence``
    needs to exercise ``ChunkLookup``'s cross-engagement check over real HTTP, not just fakes."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "other_engagement": str(uuid.uuid4()),
        "auditor_user": str(uuid.uuid4()),
        "fraud_analyst_user": str(uuid.uuid4()),
        "compliance_manager_user": str(uuid.uuid4()),
        "cae_user": str(uuid.uuid4()),
        "document": str(uuid.uuid4()),
        "chunk": str(uuid.uuid4()),
        "other_document": str(uuid.uuid4()),
        "other_chunk": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    oids = {
        "auditor_user": f"entra-auditor-{suffix}",
        "fraud_analyst_user": f"entra-fraud-{suffix}",
        "compliance_manager_user": f"entra-compliance-{suffix}",
        "cae_user": f"entra-cae-{suffix}",
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
        for key in (
            "auditor_user",
            "fraud_analyst_user",
            "compliance_manager_user",
            "cae_user",
        ):
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
            ("fraud_analyst_user", "FraudAnalyst"),
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
        # The auditor is also a member of a second, unrelated engagement — needed to exercise the
        # cross-engagement evidence check (both engagements are individually authorized reads for
        # this user; only the application-level check in ReportingService should reject mixing
        # them).
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["other_engagement"], "user_id": ids["auditor_user"]},
        )
        for eng_key, doc_key, chunk_key in (
            ("engagement", "document", "chunk"),
            ("other_engagement", "other_document", "other_chunk"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO ingestion.documents "
                    "(id, engagement_id, original_filename, storage_uri, sha256_hash, "
                    " mime_type, status, ingested_by) "
                    "VALUES (:id, :engagement_id, 'f.txt', 'uri', :hash, 'text/plain', "
                    "        'parsed', :ingested_by)"
                ),
                {
                    "id": ids[doc_key],
                    "engagement_id": ids[eng_key],
                    "hash": f"hash-{doc_key}",
                    "ingested_by": ids["auditor_user"],
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO ingestion.chunks "
                    "(id, document_id, engagement_id, chunk_index, text, char_start, char_end) "
                    "VALUES (:id, :document_id, :engagement_id, 0, 'chunk text', 0, 10)"
                ),
                {
                    "id": ids[chunk_key],
                    "document_id": ids[doc_key],
                    "engagement_id": ids[eng_key],
                },
            )

    ids.update(oids)
    yield ids

    async with admin_engine.begin() as conn:
        # audit_trail.events has an FK into identity.engagements — must be cleared before the
        # engagement itself, or that DELETE fails with a foreign-key violation (confirm/reject
        # writes an audit event).
        await conn.execute(text("DELETE FROM audit_trail.events"))
        await conn.execute(text("DELETE FROM reporting.report_findings"))
        await conn.execute(text("DELETE FROM reporting.reports"))
        await conn.execute(text("DELETE FROM reporting.finding_evidence"))
        await conn.execute(text("DELETE FROM reporting.findings"))
        await conn.execute(text("DELETE FROM ingestion.chunks"))
        await conn.execute(text("DELETE FROM ingestion.documents"))
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text(
                "DELETE FROM identity.users WHERE entra_object_id IN "
                "(:a, :b, :c, :d, :e)"
            ),
            {
                "a": oids["auditor_user"],
                "b": oids["fraud_analyst_user"],
                "c": oids["compliance_manager_user"],
                "d": oids["cae_user"],
                "e": oids["outsider"],
            },
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


def test_auditor_can_create_a_finding_and_it_starts_as_draft(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={
            "title": "Unapproved vendor payment",
            "description": "Payment issued without a matching purchase order.",
            "severity": "high",
            "control_id": "AP-04",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["title"] == "Unapproved vendor payment"


def test_outsider_cannot_create_a_finding(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["outsider"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "low"},
    )

    assert response.status_code == 403


def test_cae_cannot_create_a_finding(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """Per the RBAC matrix, CAE may read findings but not author them — only Auditor,
    Fraud Analyst, and Compliance Manager may run analysis / create findings."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["cae_user"])

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "low"},
    )

    assert response.status_code == 403


def test_auditor_can_confirm_a_finding(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    auditor_headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=auditor_headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/confirm",
        headers=auditor_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_compliance_manager_cannot_confirm_a_finding(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """Per the RBAC matrix, confirming/rejecting a finding (HITL) is Auditor/Fraud
    Analyst only — Compliance Manager can author a draft but not disposition it."""
    engagement_id = seeded_engagement["engagement"]
    auditor_headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    compliance_headers = _auth_header(
        rsa_keypair, seeded_engagement["compliance_manager_user"]
    )
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=auditor_headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/confirm",
        headers=compliance_headers,
    )

    assert response.status_code == 403


def test_confirming_an_already_confirmed_finding_is_rejected(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()
    client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/confirm", headers=headers
    )

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/confirm", headers=headers
    )

    assert response.status_code == 409


def test_rejecting_a_finding_without_a_reason_is_rejected(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """US-06: rejecting a finding requires a documented reason — enforced both by the request
    schema (empty string) and, for whitespace-only input, by the application layer."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/reject",
        headers=headers,
        json={"disposition_reason": ""},
    )

    assert response.status_code == 422


def test_rejecting_a_finding_with_a_reason_succeeds(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/reject",
        headers=headers,
        json={"disposition_reason": "Not supported by the underlying evidence."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["disposition_reason"] == "Not supported by the underlying evidence."


def test_attach_evidence_from_the_same_engagement_succeeds(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/evidence",
        headers=headers,
        json={"chunk_id": seeded_engagement["chunk"], "citation_text": "see clause 4.2"},
    )

    assert response.status_code == 201
    evidence_list = client.get(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/evidence", headers=headers
    ).json()
    assert len(evidence_list) == 1
    assert evidence_list[0]["chunk_id"] == seeded_engagement["chunk"]


def test_attach_evidence_from_a_different_engagement_is_rejected(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """The auditor user is a legitimate member of *both* engagements in this fixture — proving
    that citing the *other* engagement's chunk on *this* engagement's finding is rejected even
    though both reads are individually RLS-authorized for this user (see ``ChunkLookup``'s
    docstring)."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    created = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "t", "description": "d", "severity": "medium"},
    ).json()

    response = client.post(
        f"/v1/engagements/{engagement_id}/findings/{created['id']}/evidence",
        headers=headers,
        json={"chunk_id": seeded_engagement["other_chunk"], "citation_text": "wrong engagement"},
    )

    assert response.status_code == 422
    assert response.json()["type"] == "https://auditmind.ai/errors/chunk-engagement-mismatch"


def test_generate_report_only_includes_confirmed_findings_end_to_end(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["auditor_user"])
    draft = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "still draft", "description": "d", "severity": "low"},
    ).json()
    to_confirm = client.post(
        f"/v1/engagements/{engagement_id}/findings",
        headers=headers,
        json={"title": "will be confirmed", "description": "d", "severity": "high"},
    ).json()
    client.post(
        f"/v1/engagements/{engagement_id}/findings/{to_confirm['id']}/confirm", headers=headers
    )

    response = client.post(f"/v1/engagements/{engagement_id}/reports", headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["finding_ids"] == [to_confirm["id"]]
    assert draft["id"] not in body["finding_ids"]
    assert body["exported_uri"] is None
    assert "will be confirmed" in body["body_markdown"]
    assert "still draft" not in body["body_markdown"]

    fetched = client.get(f"/v1/engagements/{engagement_id}/reports/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["finding_ids"] == [to_confirm["id"]]
    assert fetched.json()["body_markdown"] == body["body_markdown"]
