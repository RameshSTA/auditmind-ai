"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the
real local Postgres and real Neo4j, exercising the fraud-scoring ensemble — including the
cross-context composition where a vendor resolved into the knowledge graph feeds this context's
graph-centrality signal, proven end to end rather than assumed to work just because each
context's unit tests pass independently.
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
        "member_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    member_entra_oid = f"entra-riskscore-member-{suffix}"

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Test Tenant')"),
            {"id": ids["tenant"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagements (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'engagement')"
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
    yield ids

    async with admin_engine.begin() as conn:
        # kg.entity_candidates FKs into risk.transactions — must go first, and
        # kg.entity_resolution_map FKs into kg.entity_candidates, so first of all.
        await conn.execute(text("DELETE FROM kg.entity_resolution_map"))
        await conn.execute(text("DELETE FROM kg.entity_candidates"))
        await conn.execute(text("DELETE FROM risk.risk_scores"))
        await conn.execute(text("DELETE FROM risk.anomalies"))
        await conn.execute(text("DELETE FROM risk.transactions"))
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE entra_object_id = :oid"),
            {"oid": member_entra_oid},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id = :id"), {"id": ids["engagement"]}
        )
        await conn.execute(
            text("DELETE FROM identity.tenants WHERE id = :id"), {"id": ids["tenant"]}
        )


@pytest_asyncio.fixture(autouse=True)
async def clean_neo4j(seeded_engagement: dict[str, str]) -> AsyncIterator[None]:
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
            "MATCH (n) WHERE n.engagement_id = $e DETACH DELETE n",
            e=seeded_engagement["engagement"],
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


def test_compute_risk_scores_returns_one_score_per_transaction(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "500.00")
    _import_transaction(client, headers, engagement_id, "Acme Corp", "700.00")

    response = client.post(f"/v1/engagements/{engagement_id}/risk/score", headers=headers)

    assert response.status_code == 201
    scores = response.json()
    assert len(scores) == 2
    assert all(s["subject_type"] == "transaction" for s in scores)


def test_compute_risk_scores_is_idempotent(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "500.00")

    client.post(f"/v1/engagements/{engagement_id}/risk/score", headers=headers)
    client.post(f"/v1/engagements/{engagement_id}/risk/score", headers=headers)

    response = client.get(f"/v1/engagements/{engagement_id}/risk-scores", headers=headers)
    assert len(response.json()) == 1  # refreshed in place, not accumulated


def test_risk_score_incorporates_rule_engine_anomaly(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "5000.00")  # round dollar
    client.post(f"/v1/engagements/{engagement_id}/risk/scan", headers=headers)

    response = client.post(f"/v1/engagements/{engagement_id}/risk/score", headers=headers)

    scores = response.json()
    assert "rule_engine" in scores[0]["contributing_factors"]


def test_risk_score_incorporates_graph_centrality_after_knowledge_graph_resolution(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """The cross-context proof: resolving vendors into the knowledge graph makes the
    graph-centrality signal available to risk scoring — independently-tested code from two
    bounded contexts composing correctly through the real Neo4j graph, not just through
    matching fakes in each context's own unit tests."""
    engagement_id = seeded_engagement["engagement"]
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])
    _import_transaction(client, headers, engagement_id, "Acme Corp", "500.00")

    before = client.post(f"/v1/engagements/{engagement_id}/risk/score", headers=headers).json()
    assert "graph_centrality" not in before[0]["contributing_factors"]

    resolve_response = client.post(
        f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", headers=headers
    )
    assert resolve_response.status_code == 200

    after = client.post(f"/v1/engagements/{engagement_id}/risk/score", headers=headers).json()
    assert "graph_centrality" in after[0]["contributing_factors"]


def test_non_member_cannot_compute_risk_scores(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    other_engagement_id = str(uuid.uuid4())
    headers = _auth_header(rsa_keypair, seeded_engagement["member_entra_oid"])

    response = client.post(f"/v1/engagements/{other_engagement_id}/risk/score", headers=headers)

    assert response.status_code == 403
