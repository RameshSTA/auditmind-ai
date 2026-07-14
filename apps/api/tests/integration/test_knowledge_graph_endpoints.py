"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the
real local Postgres and real Neo4j, exercising transaction import (Increment 05) → vendor
resolution → vendor listing/detail (Increment 09). Only the Entra JWKS network call is replaced,
as in the other endpoint test files.
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
    not os.environ.get("AUDITMIND_MIGRATION_DATABASE_URL")
    or not os.environ.get("AUDITMIND_NEO4J_URI"),
    reason="Requires real Postgres and Neo4j instances — set AUDITMIND_MIGRATION_DATABASE_URL "
    "and AUDITMIND_NEO4J_URI to run.",
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
    member_entra_oid = f"entra-kg-member-{suffix}"

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
        await conn.execute(text("DELETE FROM kg.entity_resolution_map"))
        await conn.execute(text("DELETE FROM kg.entity_candidates"))
        await conn.execute(text("DELETE FROM risk.transactions"))
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


@pytest_asyncio.fixture(autouse=True)
async def clean_neo4j(seeded_engagement: dict[str, str]) -> AsyncIterator[None]:
    """Nodes this test run creates are engagement-scoped, so cleanup mirrors the Postgres
    fixture's own teardown rather than truncating the whole graph."""
    yield
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        os.environ["AUDITMIND_NEO4J_URI"],
        auth=(
            os.environ.get("AUDITMIND_NEO4J_USER", "neo4j"),
            os.environ.get("AUDITMIND_NEO4J_PASSWORD", ""),
        ),
    )
    async with driver.session() as session:
        await session.run(
            "MATCH (n) WHERE n.engagement_id IN [$e1, $e2] DETACH DELETE n",
            e1=seeded_engagement["engagement"],
            e2=seeded_engagement["other_engagement"],
        )
    await driver.close()


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
    monkeypatch.setenv("AUDITMIND_NEO4J_URI", os.environ["AUDITMIND_NEO4J_URI"])
    monkeypatch.setenv("AUDITMIND_NEO4J_USER", os.environ.get("AUDITMIND_NEO4J_USER", "neo4j"))
    monkeypatch.setenv(
        "AUDITMIND_NEO4J_PASSWORD", os.environ.get("AUDITMIND_NEO4J_PASSWORD", "")
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


def _import_transaction(
    client: TestClient, headers: dict[str, str], engagement_id: str, vendor_name: str, amount: str
) -> None:
    response = client.post(
        f"/v1/engagements/{engagement_id}/transactions",
        headers=headers,
        json={
            "transactions": [
                {
                    "amount": amount,
                    "currency": "USD",
                    "transaction_date": "2026-01-01",
                    "vendor_name": vendor_name,
                }
            ]
        },
    )
    assert response.status_code == 201


def test_resolve_vendors_returns_the_newly_resolved_count(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "100.00")
    _import_transaction(client, headers, engagement_id, "acme corp", "150.00")

    response = client.post(
        f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["newly_resolved_count"] == 2


def test_resolve_vendors_is_idempotent(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "100.00")

    first = client.post(
        f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", headers=headers
    )
    second = client.post(
        f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", headers=headers
    )

    assert first.json()["newly_resolved_count"] == 1
    assert second.json()["newly_resolved_count"] == 0


def test_list_vendors_merges_name_variants_into_one_entity_with_aggregate_stats(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "100.00")
    _import_transaction(client, headers, engagement_id, "  ACME CORP  ", "150.00")
    client.post(f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", headers=headers)

    response = client.get(
        f"/v1/engagements/{engagement_id}/knowledge-graph/vendors", headers=headers
    )

    assert response.status_code == 200
    vendors = response.json()
    assert len(vendors) == 1
    assert vendors[0]["transaction_count"] == 2
    assert vendors[0]["total_amount_by_currency"]["USD"] == "250.00"


def test_get_vendor_returns_the_vendor_network(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "100.00")
    client.post(f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", headers=headers)
    vendor_id = client.get(
        f"/v1/engagements/{engagement_id}/knowledge-graph/vendors", headers=headers
    ).json()[0]["id"]

    response = client.get(
        f"/v1/engagements/{engagement_id}/knowledge-graph/vendors/{vendor_id}", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == vendor_id
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["amount"] == "100.00"


def test_get_vendor_returns_404_for_an_unknown_vendor_id(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])

    response = client.get(
        f"/v1/engagements/{engagement_id}/knowledge-graph/vendors/{uuid.uuid4()}", headers=headers
    )

    assert response.status_code == 404


def test_non_member_cannot_resolve_vendors(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    other_engagement_id = seeded_engagement["other_engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])

    response = client.post(
        f"/v1/engagements/{other_engagement_id}/knowledge-graph/resolve", headers=headers
    )

    assert response.status_code == 403
