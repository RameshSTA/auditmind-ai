"""Full-stack HTTP integration tests — real requests, real Postgres, real RLS. The graph itself
runs against a fake ``LlmClient`` and a fake ``RagApiClient``, both injected via dependency
overrides, deliberately independent of whatever real provider key or reachable apps/api instance
this environment may or may not have — everything above those two adapters (routing, HTTP, RBAC,
RLS, persistence, the HITL resume path) is exercised for real."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import agent_orchestrator.shared.database as database_module
from agent_orchestrator.interface.dependencies import get_api_client, get_llm_client
from agent_orchestrator.main import create_app
from agent_orchestrator.shared.auth import JWKSClient
from agent_orchestrator.shared.settings import get_settings
from tests.conftest import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    FakeApiClient,
    FakeLlmClient,
    KeyMaterial,
    make_token,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGENT_MIGRATION_DATABASE_URL"),
    reason="Requires a real Postgres instance — set AGENT_MIGRATION_DATABASE_URL to run.",
)


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["AGENT_MIGRATION_DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def engagement_with_auditor(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    """Seeds one engagement with two members of *different* database roles — an Auditor and a
    CAE. Authorization in this service is resolved entirely from the ``identity.engagement_members``
    row, never from a caller's JWT ``roles`` claim, so proving a role-gated 403 requires a member
    whose *database* role is actually disallowed, not just a token that claims to be some other
    role for the same identity."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "user": str(uuid.uuid4()),
        "cae_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    entra_oid = f"entra-auditor-{suffix}"
    cae_entra_oid = f"entra-cae-{suffix}"

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Test Tenant')"),
            {"id": ids["tenant"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagements (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'Engagement')"
            ),
            {"id": ids["engagement"], "tenant_id": ids["tenant"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                "VALUES (:id, :entra_oid, 'Auditor', :email)"
            ),
            {"id": ids["user"], "entra_oid": entra_oid, "email": f"auditor-{suffix}@example.com"},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                "VALUES (:id, :entra_oid, 'CAE', :email)"
            ),
            {
                "id": ids["cae_user"],
                "entra_oid": cae_entra_oid,
                "email": f"cae-{suffix}@example.com",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["engagement"], "user_id": ids["user"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'CAE')"
            ),
            {"engagement_id": ids["engagement"], "user_id": ids["cae_user"]},
        )

    ids["entra_oid"] = entra_oid
    ids["cae_entra_oid"] = cae_entra_oid
    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM agent.hitl_interrupts WHERE engagement_id = :e"),
            {"e": ids["engagement"]},
        )
        await conn.execute(
            text("DELETE FROM agent.runs WHERE engagement_id = :e"), {"e": ids["engagement"]}
        )
        await conn.execute(
            text("DELETE FROM identity.engagement_members WHERE engagement_id = :e"),
            {"e": ids["engagement"]},
        )
        await conn.execute(
            text("DELETE FROM identity.users WHERE id IN (:u1, :u2)"),
            {"u1": ids["user"], "u2": ids["cae_user"]},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id = :e"), {"e": ids["engagement"]}
        )
        await conn.execute(text("DELETE FROM identity.tenants WHERE id = :t"), {"t": ids["tenant"]})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, rsa_keypair: KeyMaterial) -> Iterator[TestClient]:
    """A test client against the real settings/database/auth stack, with only the LLM client
    swapped for a fake (via FastAPI's dependency-override mechanism) — everything else (session,
    repositories, RBAC, RLS) is real, proving the whole stack above the gateway adapter end to
    end. Mirrors ``apps/api``'s identical integration-test client fixture."""
    monkeypatch.setenv("AGENT_ENTRA_CLIENT_ID", TEST_AUDIENCE)
    monkeypatch.setenv("AGENT_ENTRA_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AGENT_ENTRA_JWKS_URI", "https://fake-entra.example/keys")
    monkeypatch.setenv("AGENT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AGENT_DATABASE_HOST", os.environ.get("AGENT_TEST_DB_HOST", "localhost"))
    monkeypatch.setenv("AGENT_DATABASE_PORT", os.environ.get("AGENT_TEST_DB_PORT", "5433"))
    monkeypatch.setenv("AGENT_DATABASE_NAME", os.environ.get("AGENT_TEST_DB_NAME", "auditmind"))
    monkeypatch.setenv(
        "AGENT_DATABASE_APP_USER", os.environ.get("AGENT_TEST_APP_USER", "auditmind_app")
    )
    monkeypatch.setenv(
        "AGENT_DATABASE_APP_PASSWORD",
        os.environ.get("AGENT_TEST_APP_PASSWORD", "auditmind_app_local_dev_only"),
    )
    get_settings.cache_clear()

    # The DB engine is a process-wide singleton (shared/database.py) — reset it so each test
    # picks up this fixture's settings rather than a previous test's cached engine.
    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_factory", None)

    async def fake_refresh(self: JWKSClient) -> None:
        self._cached_keys = {"keys": [rsa_keypair.jwk_dict]}
        self._cached_at = time.monotonic()

    monkeypatch.setattr(JWKSClient, "_refresh", fake_refresh)

    app = create_app()
    fake_llm = FakeLlmClient(default_text="a cited draft", smart_defaults=True)
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    # The RAG tool client is overridden the same way — these tests prove the HTTP/RBAC/RLS/HITL
    # stack end to end, deliberately independent of whether apps/api is reachable from this
    # process, the same boundary the fake LLM client draws.
    app.dependency_overrides[get_api_client] = lambda: FakeApiClient()

    with TestClient(app) as test_client:
        yield test_client


def _auth_headers(rsa_keypair: KeyMaterial, *, subject: str, roles: list[str]) -> dict[str, str]:
    token = make_token(
        rsa_keypair, subject=subject, roles=roles, audience=TEST_AUDIENCE, issuer=TEST_ISSUER
    )
    return {"Authorization": f"Bearer {token}"}


def test_healthz_is_always_200() -> None:
    with TestClient(create_app()) as test_client:
        response = test_client.get("/healthz")
    assert response.status_code == 200


def test_start_run_and_read_it_back(
    client: TestClient,
    rsa_keypair: KeyMaterial,
    engagement_with_auditor: dict[str, str],
) -> None:
    headers = _auth_headers(
        rsa_keypair, subject=engagement_with_auditor["entra_oid"], roles=["Auditor"]
    )
    engagement_id = engagement_with_auditor["engagement"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs",
        json={"use_case": "control_test", "task": "Investigate vendor concentration"},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "awaiting_human_review"
    assert body["engagement_id"] == engagement_id

    get_response = client.get(
        f"/v1/engagements/{engagement_id}/agent-runs/{body['id']}", headers=headers
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == body["id"]


def test_start_run_rejects_a_non_member(client: TestClient, rsa_keypair: KeyMaterial) -> None:
    headers = _auth_headers(rsa_keypair, subject="entra-outsider-nobody", roles=["Auditor"])

    response = client.post(
        f"/v1/engagements/{uuid.uuid4()}/agent-runs",
        json={"use_case": "control_test", "task": "t"},
        headers=headers,
    )

    # A token subject with no matching identity.users row is rejected at the identity-resolution
    # step (401), before the engagement-membership check is even reached.
    assert response.status_code == 401


def test_start_run_rejects_a_role_not_permitted_to_author(
    client: TestClient,
    rsa_keypair: KeyMaterial,
    engagement_with_auditor: dict[str, str],
) -> None:
    """The engagement's *CAE* member (read-only, per the seeded ``identity.engagement_members``
    row) is denied — role gating is enforced server-side against that membership row, not
    whatever role a caller's own token happens to claim. The token here honestly carries 'CAE'
    too, matching the database, to keep the test's premise unambiguous."""
    headers = _auth_headers(
        rsa_keypair, subject=engagement_with_auditor["cae_entra_oid"], roles=["CAE"]
    )

    response = client.post(
        f"/v1/engagements/{engagement_with_auditor['engagement']}/agent-runs",
        json={"use_case": "control_test", "task": "t"},
        headers=headers,
    )

    assert response.status_code == 403


def test_hitl_resolve_approve_resumes_the_graph_and_completes_the_run(
    client: TestClient,
    rsa_keypair: KeyMaterial,
    engagement_with_auditor: dict[str, str],
) -> None:
    headers = _auth_headers(
        rsa_keypair, subject=engagement_with_auditor["entra_oid"], roles=["Auditor"]
    )
    engagement_id = engagement_with_auditor["engagement"]

    start_response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs",
        json={"use_case": "control_test", "task": "Investigate"},
        headers=headers,
    )
    assert start_response.status_code == 201
    run_id = start_response.json()["id"]

    interrupts_response = client.get(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}/hitl-interrupts", headers=headers
    )
    assert interrupts_response.status_code == 200
    interrupts = interrupts_response.json()
    assert len(interrupts) == 1
    assert interrupts[0]["decision"] is None
    interrupt_id = interrupts[0]["id"]

    resolve_response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}"
        f"/hitl-interrupts/{interrupt_id}/resolve",
        json={"decision": "approve"},
        headers=headers,
    )

    assert resolve_response.status_code == 200, resolve_response.text
    assert resolve_response.json()["decision"] == "approve"

    run_response = client.get(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}", headers=headers
    )
    assert run_response.json()["status"] == "completed"

    # The interrupt is no longer open once resolved.
    interrupts_after = client.get(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}/hitl-interrupts", headers=headers
    )
    assert interrupts_after.json() == []


def test_hitl_resolve_reject_requires_a_reason(
    client: TestClient,
    rsa_keypair: KeyMaterial,
    engagement_with_auditor: dict[str, str],
) -> None:
    headers = _auth_headers(
        rsa_keypair, subject=engagement_with_auditor["entra_oid"], roles=["Auditor"]
    )
    engagement_id = engagement_with_auditor["engagement"]

    start_response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs",
        json={"use_case": "control_test", "task": "Investigate"},
        headers=headers,
    )
    run_id = start_response.json()["id"]
    interrupts = client.get(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}/hitl-interrupts", headers=headers
    ).json()
    interrupt_id = interrupts[0]["id"]

    response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}"
        f"/hitl-interrupts/{interrupt_id}/resolve",
        json={"decision": "reject"},
        headers=headers,
    )

    assert response.status_code == 422


def test_hitl_resolve_rejects_a_cae_member(
    client: TestClient,
    rsa_keypair: KeyMaterial,
    engagement_with_auditor: dict[str, str],
) -> None:
    """The sign-off gate is restricted to Auditor/FraudAnalyst — a CAE member of the same
    engagement (read-only, per the seeded membership row) is denied, the same role split
    ``apps/api``'s finding confirm/reject endpoints enforce."""
    auditor_headers = _auth_headers(
        rsa_keypair, subject=engagement_with_auditor["entra_oid"], roles=["Auditor"]
    )
    engagement_id = engagement_with_auditor["engagement"]
    start_response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs",
        json={"use_case": "control_test", "task": "Investigate"},
        headers=auditor_headers,
    )
    run_id = start_response.json()["id"]
    interrupts = client.get(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}/hitl-interrupts",
        headers=auditor_headers,
    ).json()
    interrupt_id = interrupts[0]["id"]

    cae_headers = _auth_headers(
        rsa_keypair, subject=engagement_with_auditor["cae_entra_oid"], roles=["CAE"]
    )
    response = client.post(
        f"/v1/engagements/{engagement_id}/agent-runs/{run_id}"
        f"/hitl-interrupts/{interrupt_id}/resolve",
        json={"decision": "approve"},
        headers=cae_headers,
    )

    assert response.status_code == 403
