"""Full-stack integration tests: real HTTP requests, through the real FastAPI app, against the
real local Postgres instance — the actual chain a production request takes (JWT validation → DB
user resolution/JIT-provisioning → RLS context binding → engagement membership check), with only
the Entra JWKS network call replaced (as in ``test_app.py``).

Skips automatically if no real Postgres is configured, for the same reason as
``test_identity_rls.py``.
"""

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
async def seeded_member(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    """One user, pre-provisioned (not JIT — proving the "existing user" path), who is a member of
    one engagement, plus a second existing member of that same engagement (the roster endpoint's
    tests need a peer to prove roster visibility, not just the caller's own row); and a third,
    never-provisioned Entra identity representing a user who has never logged in before, to
    exercise JIT provisioning through the real HTTP endpoint."""
    ids = {
        "tenant": str(uuid.uuid4()),
        "engagement": str(uuid.uuid4()),
        "member_user": str(uuid.uuid4()),
        "peer_user": str(uuid.uuid4()),
    }
    suffix = uuid.uuid4().hex[:8]
    member_entra_oid = f"entra-member-{suffix}"
    peer_entra_oid = f"entra-peer-{suffix}"
    new_user_entra_oid = f"entra-newuser-{suffix}"

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
        # Suffixed emails — `identity.users.email` has a real uniqueness constraint (migration
        # d4a19e6b7f31, self-service signup) now, so a fixed literal here would collide with
        # itself across runs if a prior run's teardown never completed (e.g. a crash).
        member_email = f"m-{suffix}@example.com"
        peer_email = f"peer-{suffix}@example.com"
        await conn.execute(
            text(
                "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                "VALUES (:id, :entra_oid, 'Existing Member', :email)"
            ),
            {"id": ids["member_user"], "entra_oid": member_entra_oid, "email": member_email},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.users (id, entra_object_id, display_name, email) "
                "VALUES (:id, :entra_oid, 'Peer Member', :email)"
            ),
            {"id": ids["peer_user"], "entra_oid": peer_entra_oid, "email": peer_email},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'Auditor')"
            ),
            {"engagement_id": ids["engagement"], "user_id": ids["member_user"]},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                "VALUES (:engagement_id, :user_id, 'FraudAnalyst')"
            ),
            {"engagement_id": ids["engagement"], "user_id": ids["peer_user"]},
        )

    ids["member_entra_oid"] = member_entra_oid
    ids["peer_entra_oid"] = peer_entra_oid
    ids["new_user_entra_oid"] = new_user_entra_oid
    ids["member_email"] = member_email
    ids["peer_email"] = peer_email
    yield ids

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM identity.engagement_members"))
        await conn.execute(
            text("DELETE FROM identity.users WHERE entra_object_id IN (:a, :b, :c)"),
            {"a": member_entra_oid, "b": peer_entra_oid, "c": new_user_entra_oid},
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id = :id"), {"id": ids["engagement"]}
        )
        await conn.execute(
            text("DELETE FROM identity.tenants WHERE id = :id"), {"id": ids["tenant"]}
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, rsa_keypair: KeyMaterial) -> Iterator[TestClient]:
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
    get_settings.cache_clear()

    # The DB engine is a process-wide singleton (shared/database.py) so it's built once per
    # process, not once per request — reset it here so each test picks up this fixture's settings
    # rather than whatever a previous test in the same pytest session already cached.
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


def test_existing_member_sees_their_own_engagement_via_http(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_member: dict[str, str]
) -> None:
    token = make_token(rsa_keypair, subject=seeded_member["member_entra_oid"], roles=["Auditor"])

    response = client.get("/v1/me/engagements", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["engagement_id"] == seeded_member["engagement"]
    assert body[0]["role"] == "Auditor"


def test_member_can_confirm_their_role_on_their_own_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_member: dict[str, str]
) -> None:
    token = make_token(rsa_keypair, subject=seeded_member["member_entra_oid"], roles=["Auditor"])
    engagement_id = seeded_member["engagement"]

    response = client.get(
        f"/v1/engagements/{engagement_id}/membership", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"engagement_id": engagement_id, "role": "Auditor"}


def test_non_member_is_denied_access_to_someone_elses_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_member: dict[str, str]
) -> None:
    """A brand-new Entra identity (JIT-provisioned on this very request) that has never been
    granted access to the seeded engagement must be denied — proving the membership check is
    real, not merely "any authenticated user can see anything"."""
    token = make_token(rsa_keypair, subject=seeded_member["new_user_entra_oid"], roles=["Auditor"])
    engagement_id = seeded_member["engagement"]

    response = client.get(
        f"/v1/engagements/{engagement_id}/membership", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.json()["type"] == "https://auditmind.ai/errors/authorization-error"


def test_a_new_entra_identity_is_jit_provisioned_and_sees_no_engagements(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_member: dict[str, str]
) -> None:
    token = make_token(rsa_keypair, subject=seeded_member["new_user_entra_oid"], roles=["Auditor"])

    response = client.get("/v1/me/engagements", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_member_sees_the_full_roster_including_a_peer(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_member: dict[str, str]
) -> None:
    """Administration's roster endpoint, end to end: the caller sees not just their own row but
    their peer's too, with real profile fields (not just a bare user id)."""
    token = make_token(rsa_keypair, subject=seeded_member["member_entra_oid"], roles=["Auditor"])
    engagement_id = seeded_member["engagement"]

    response = client.get(
        f"/v1/engagements/{engagement_id}/members", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    roles_by_email = {entry["email"]: entry["role"] for entry in body}
    assert roles_by_email == {
        seeded_member["member_email"]: "Auditor",
        seeded_member["peer_email"]: "FraudAnalyst",
    }


def test_non_member_is_denied_the_roster_of_someone_elses_engagement(
    client: TestClient, rsa_keypair: KeyMaterial, seeded_member: dict[str, str]
) -> None:
    token = make_token(rsa_keypair, subject=seeded_member["new_user_entra_oid"], roles=["Auditor"])
    engagement_id = seeded_member["engagement"]

    response = client.get(
        f"/v1/engagements/{engagement_id}/members", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


@pytest_asyncio.fixture
async def signup_engagement(admin_engine: AsyncEngine) -> AsyncIterator[str]:
    """A real tenant + engagement for self-service signup to auto-join — the test-time stand-in
    for the fixed demo engagement ``Settings.default_engagement_id`` points at in a real
    environment. The ``client`` fixture below points that setting at this engagement's id."""
    tenant_id = str(uuid.uuid4())
    engagement_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO identity.tenants (id, name) VALUES (:id, 'Signup Test Tenant')"),
            {"id": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO identity.engagements (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'Signup Test Engagement')"
            ),
            {"id": engagement_id, "tenant_id": tenant_id},
        )
    yield engagement_id
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM identity.engagement_members WHERE engagement_id = :id"),
            {"id": engagement_id},
        )
        await conn.execute(
            text("DELETE FROM identity.credentials WHERE user_id IN "
                 "(SELECT id FROM identity.users WHERE email LIKE 'signup-test-%')"),
        )
        await conn.execute(
            text("DELETE FROM identity.users WHERE email LIKE 'signup-test-%'"),
        )
        await conn.execute(
            text("DELETE FROM identity.engagements WHERE id = :id"), {"id": engagement_id}
        )
        await conn.execute(text("DELETE FROM identity.tenants WHERE id = :id"), {"id": tenant_id})


@pytest.fixture
def signup_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, signup_engagement: str
) -> TestClient:
    """The same app/DB the ``client`` fixture builds, with self-service signup pointed at a real,
    disposable engagement instead of production's fixed demo engagement id."""
    monkeypatch.setenv("AUDITMIND_DEFAULT_ENGAGEMENT_ID", signup_engagement)
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


def _unique_email() -> str:
    return f"signup-test-{uuid.uuid4().hex[:12]}@example.com"


def test_register_creates_a_real_account_and_auto_joins_the_engagement(
    signup_client: TestClient, signup_engagement: str
) -> None:
    email = _unique_email()

    response = signup_client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "New Auditor",
            "role": "Auditor",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "Auditor"
    assert body["engagement_id"] == signup_engagement
    assert body["subject"].startswith("local:")


def test_register_rejects_a_duplicate_email(signup_client: TestClient) -> None:
    email = _unique_email()
    payload = {
        "email": email,
        "password": "correct-horse-battery",
        "display_name": "First",
        "role": "Auditor",
    }
    first = signup_client.post("/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = signup_client.post("/v1/auth/register", json={**payload, "display_name": "Second"})

    assert second.status_code == 422
    assert "already exists" in second.json()["detail"]


def test_register_rejects_the_admin_role(signup_client: TestClient) -> None:
    response = signup_client.post(
        "/v1/auth/register",
        json={
            "email": _unique_email(),
            "password": "correct-horse-battery",
            "display_name": "Would-be Admin",
            "role": "Admin",
        },
    )

    assert response.status_code == 422


def test_login_succeeds_with_the_registered_password_and_returns_the_engagement_role(
    signup_client: TestClient, signup_engagement: str
) -> None:
    email = _unique_email()
    signup_client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Login Test",
            "role": "FraudAnalyst",
        },
    )

    response = signup_client.post(
        "/v1/auth/login", json={"email": email, "password": "correct-horse-battery"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "FraudAnalyst"
    assert body["engagement_id"] == signup_engagement


def test_login_rejects_the_wrong_password(signup_client: TestClient) -> None:
    email = _unique_email()
    signup_client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Login Test",
            "role": "Auditor",
        },
    )

    response = signup_client.post(
        "/v1/auth/login", json={"email": email, "password": "wrong-password"}
    )

    assert response.status_code == 401


def test_login_rejects_an_unknown_email(signup_client: TestClient) -> None:
    response = signup_client.post(
        "/v1/auth/login", json={"email": "nobody-signup-test@example.com", "password": "whatever1"}
    )

    assert response.status_code == 401


def test_a_freshly_registered_user_can_use_their_token_end_to_end(
    signup_client: TestClient, rsa_keypair: KeyMaterial, signup_engagement: str
) -> None:
    """The whole point of self-service signup: the identity it creates is usable exactly like any
    JIT-provisioned Entra identity for every other endpoint — same JWT `sub` claim, same RLS."""
    email = _unique_email()
    register_response = signup_client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "End To End",
            "role": "CAE",
        },
    )
    subject = register_response.json()["subject"]
    token = make_token(rsa_keypair, subject=subject, roles=["CAE"])

    response = signup_client.get(
        "/v1/me/engagements", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["engagement_id"] == signup_engagement
    assert body[0]["role"] == "CAE"
