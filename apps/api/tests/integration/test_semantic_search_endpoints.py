"""Full-stack integration tests: real HTTP requests through the real FastAPI app, against the real
local Postgres and real pgvector, proving the vector-embedding leg (Increment 08) actually stores
and ranks embeddings correctly — end to end through ``POST .../embeddings`` and
``GET .../search/semantic``.

**Deliberate, documented deviation from every other integration test file in this codebase**: the
real ``BgeM3EmbeddingGenerator`` (Increment 08's production adapter) is swapped for a fast,
deterministic, dependency-free fake via FastAPI's ``dependency_overrides`` — see
``FakeEmbeddingGenerator`` below for why. Everything else here is real: real HTTP requests, real
Postgres, real pgvector storage, real cosine-distance search, real Row-Level Security. The one
thing this suite cannot prove — that BGE-M3 itself produces semantically good embeddings — was
verified manually against the real model this session; see the increment doc §3 for that result.
Model *quality* evaluation belongs to Phase 9's (unbuilt) evaluation framework, not this suite.
"""

from __future__ import annotations

import hashlib
import math
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
from auditmind_api.retrieval.interface.dependencies import get_embedding_generator
from auditmind_api.shared.auth import JWKSClient
from auditmind_api.shared.settings import get_settings
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, KeyMaterial, make_token

pytestmark = pytest.mark.skipif(
    not os.environ.get("AUDITMIND_MIGRATION_DATABASE_URL"),
    reason="Requires a real Postgres instance — set AUDITMIND_MIGRATION_DATABASE_URL to run.",
)

_DIM = 1024


class FakeEmbeddingGenerator:
    """A bag-of-words embedder: each word hashes to a dimension it increments, then the vector is
    L2-normalized. Deterministic, instant, and — unlike a random hash per string — actually
    content-sensitive: two texts sharing words end up with higher cosine similarity than two that
    share none, which is exactly the property these tests need to prove pgvector's storage,
    ranking, and RLS are wired correctly. What it does *not* prove is BGE-M3's actual semantic
    quality (synonyms, paraphrase, cross-lingual matching) — that needs the real ~2.2GB model,
    infeasible to download on every CI run or every contributor's first `pytest`; see this
    module's docstring."""

    model_id = "fake-bag-of-words-test-embedder-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * _DIM
        for word in text.lower().split():
            index = int(hashlib.sha256(word.encode()).hexdigest(), 16) % _DIM
            vector[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


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
    member_entra_oid = f"entra-semantic-member-{suffix}"

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
        await conn.execute(text("DELETE FROM retrieval.chunk_embeddings"))
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
    app.dependency_overrides[get_embedding_generator] = FakeEmbeddingGenerator
    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_factory", None)


def _upload(client: TestClient, headers: dict[str, str], engagement_id: str, content: bytes) -> str:
    response = client.post(
        f"/v1/engagements/{engagement_id}/documents",
        headers=headers,
        files={"file": ("evidence.txt", content, "text/plain")},
    )
    document_id: str = response.json()["id"]
    return document_id


def test_embed_document_stores_an_embedding_per_chunk(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    document_id = _upload(client, headers, engagement_id, b"The vendor payment lacked approval.")

    response = client.post(
        f"/v1/engagements/{engagement_id}/documents/{document_id}/embeddings", headers=headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["document_id"] == document_id
    assert body["embedded_count"] == 1


def test_embed_document_is_idempotent(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    document_id = _upload(client, headers, engagement_id, b"The vendor payment lacked approval.")

    first = client.post(
        f"/v1/engagements/{engagement_id}/documents/{document_id}/embeddings", headers=headers
    )
    second = client.post(
        f"/v1/engagements/{engagement_id}/documents/{document_id}/embeddings", headers=headers
    )

    assert first.json()["embedded_count"] == 1
    assert second.json()["embedded_count"] == 0  # already embedded by this model — no-op


def test_semantic_search_finds_the_chunk_most_similar_to_the_query(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    document_id = _upload(
        client,
        headers,
        engagement_id,
        b"The vendor payment was approved without a matching purchase order.\n\n"
        b"Unrelated paragraph about office supplies and stationery budgets.",
    )
    client.post(
        f"/v1/engagements/{engagement_id}/documents/{document_id}/embeddings", headers=headers
    )

    response = client.get(
        f"/v1/engagements/{engagement_id}/search/semantic",
        headers=headers,
        params={"q": "vendor payment approved purchase order"},
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1
    assert "vendor payment" in results[0]["text"]


def test_semantic_search_returns_nothing_for_an_unembedded_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    """No embeddings have been generated yet — search finds nothing, not an error, the same
    "search what's been indexed" behavior the keyword leg already has."""
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    _upload(client, headers, engagement_id, b"Vendor payment approved.")

    response = client.get(
        f"/v1/engagements/{engagement_id}/search/semantic",
        headers=headers,
        params={"q": "vendor payment"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_semantic_search_does_not_return_another_engagements_chunks(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    other_engagement_id = seeded_engagement["other_engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )
    headers = {"Authorization": f"Bearer {token}"}
    document_id = _upload(client, headers, engagement_id, b"A perfectly normal transaction.")
    client.post(
        f"/v1/engagements/{engagement_id}/documents/{document_id}/embeddings", headers=headers
    )

    response = client.get(
        f"/v1/engagements/{other_engagement_id}/search/semantic",
        headers=headers,
        params={"q": "transaction"},
    )

    assert response.status_code == 403  # not a member of other_engagement at all


def test_semantic_search_empty_query_string_is_rejected(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_engagement: dict[str, str]
) -> None:
    engagement_id = seeded_engagement["engagement"]
    token = make_token(
        rsa_keypair, subject=seeded_engagement["member_entra_oid"], roles=["Auditor"]
    )

    response = client.get(
        f"/v1/engagements/{engagement_id}/search/semantic",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": ""},
    )

    assert response.status_code == 422
