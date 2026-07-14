"""Integration tests: the full FastAPI app, wired exactly as it runs in production, driven over
HTTP via ``TestClient`` (Phase 3 §11).

The only thing replaced is the JWKS network call — ``JWKSClient._refresh`` is patched to load the
test RSA key directly, so these tests exercise the *real* validation, middleware, exception
handling, and routing wiring end-to-end without depending on network access to a live Entra ID
tenant.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import auditmind_api.shared.database as database_module
from auditmind_api.main import create_app
from auditmind_api.shared.auth import JWKSClient
from auditmind_api.shared.settings import get_settings
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, KeyMaterial, make_token


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, rsa_keypair: KeyMaterial) -> Iterator[TestClient]:
    monkeypatch.setenv("AUDITMIND_ENTRA_CLIENT_ID", TEST_AUDIENCE)
    monkeypatch.setenv("AUDITMIND_ENTRA_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AUDITMIND_ENTRA_JWKS_URI", "https://fake-entra.example/keys")
    monkeypatch.setenv("AUDITMIND_LOG_LEVEL", "WARNING")
    # /readyz (added when Increment 02 introduced a real database) needs a reachable Postgres to
    # report ready — pointed at the same local test instance the identity/ingestion integration
    # tests use, falling back to a value that simply won't be reachable if none is configured, so
    # this file's tests keep working (readiness correctly reports 503) even with no database at
    # all, per Increment 01's original "the rest of the suite still runs without one" design.
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

    # Process-wide engine singleton (shared/database.py) — reset so this test file's settings
    # are what get used, not whatever an earlier test module already cached.
    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_factory", None)

    async def fake_refresh(self: JWKSClient) -> None:
        """Stands in for the real network call to Entra's JWKS endpoint."""
        self._cached_keys = {"keys": [rsa_keypair.jwk_dict]}
        self._cached_at = time.monotonic()

    monkeypatch.setattr(JWKSClient, "_refresh", fake_refresh)

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_factory", None)


@pytest.fixture
def client_with_unreachable_db(
    monkeypatch: pytest.MonkeyPatch, rsa_keypair: KeyMaterial
) -> Iterator[TestClient]:
    """Same as ``client``, but pointed at a port nothing is listening on — for proving the
    readiness probe's failure path, not just its happy path."""
    monkeypatch.setenv("AUDITMIND_ENTRA_CLIENT_ID", TEST_AUDIENCE)
    monkeypatch.setenv("AUDITMIND_ENTRA_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("AUDITMIND_ENTRA_JWKS_URI", "https://fake-entra.example/keys")
    monkeypatch.setenv("AUDITMIND_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("AUDITMIND_DATABASE_HOST", "localhost")
    monkeypatch.setenv("AUDITMIND_DATABASE_PORT", "1")  # nothing listens on port 1
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


# --- health endpoints ---


def test_liveness_reports_alive(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_ready(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_reports_not_ready_when_database_is_unreachable(
    client_with_unreachable_db: TestClient,
) -> None:
    """The failure path, not just the happy path — a replica that can't reach its database must
    fail readiness (and stop receiving traffic), not report a healthy 200 it can't back up."""
    response = client_with_unreachable_db.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


# --- trace id propagation (Phase 10 §1) ---


def test_trace_id_is_generated_when_caller_supplies_none(client: TestClient) -> None:
    response = client.get("/healthz")

    assert "x-trace-id" in response.headers
    assert len(response.headers["x-trace-id"]) > 0


def test_trace_id_supplied_by_caller_is_echoed_back_unchanged(client: TestClient) -> None:
    response = client.get("/healthz", headers={"x-trace-id": "caller-supplied-id"})

    assert response.headers["x-trace-id"] == "caller-supplied-id"


# --- authentication ---


def test_protected_endpoint_without_token_returns_401_problem_json(client: TestClient) -> None:
    response = client.get("/v1/me")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "https://auditmind.ai/errors/authentication-error"
    assert body["trace_id"]


def test_protected_endpoint_with_malformed_auth_header_returns_401(client: TestClient) -> None:
    response = client.get("/v1/me", headers={"Authorization": "NotBearer abc"})

    assert response.status_code == 401


def test_protected_endpoint_with_valid_token_returns_the_caller_identity(
    client: TestClient, rsa_keypair: KeyMaterial
) -> None:
    token = make_token(rsa_keypair, subject="user-99", roles=["Auditor"])

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "user-99"
    assert body["roles"] == ["Auditor"]
    assert body["tenant_id"] == "test-tenant"


def test_protected_endpoint_with_expired_token_returns_401(
    client: TestClient, rsa_keypair: KeyMaterial
) -> None:
    token = make_token(rsa_keypair, expires_in_seconds=-3600)

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


# --- authorization / RBAC (Phase 11 §2) ---


def test_admin_endpoint_denies_a_caller_without_the_admin_role(
    client: TestClient, rsa_keypair: KeyMaterial
) -> None:
    token = make_token(rsa_keypair, subject="user-99", roles=["Auditor"])

    response = client.get("/v1/admin/ping", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "https://auditmind.ai/errors/authorization-error"


def test_admin_endpoint_allows_a_caller_with_the_admin_role(
    client: TestClient, rsa_keypair: KeyMaterial
) -> None:
    token = make_token(rsa_keypair, subject="admin-1", roles=["Admin"])

    response = client.get("/v1/admin/ping", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "subject": "admin-1"}


# --- general error behavior ---


def test_unknown_route_returns_404_not_a_crash(client: TestClient) -> None:
    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
