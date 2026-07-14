"""Unit tests for Settings (Phase 3 §4: configuration from environment variables only)."""

from __future__ import annotations

import pytest

from auditmind_api.shared.settings import Settings

_ALL_AUDITMIND_VARS = [
    "AUDITMIND_ENVIRONMENT",
    "AUDITMIND_LOG_LEVEL",
    "AUDITMIND_ENTRA_TENANT_ID",
    "AUDITMIND_ENTRA_CLIENT_ID",
    "AUDITMIND_ENTRA_ISSUER",
    "AUDITMIND_ENTRA_JWKS_URI",
    "AUDITMIND_JWT_LEEWAY_SECONDS",
]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensures no AUDITMIND_* variable leaks in from the developer's actual shell, so these tests
    are deterministic regardless of what's already exported in the terminal running them."""
    for var in _ALL_AUDITMIND_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults_apply_when_no_env_vars_set() -> None:
    settings = Settings()

    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.jwt_leeway_seconds == 30
    assert settings.is_production is False


def test_environment_variables_are_read_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDITMIND_ENVIRONMENT", "prod")
    monkeypatch.setenv("AUDITMIND_ENTRA_CLIENT_ID", "abc-123")
    monkeypatch.setenv("AUDITMIND_JWT_LEEWAY_SECONDS", "60")

    settings = Settings()

    assert settings.environment == "prod"
    assert settings.entra_client_id == "abc-123"
    assert settings.jwt_leeway_seconds == 60
    assert settings.is_production is True


def test_settings_are_immutable() -> None:
    settings = Settings()

    with pytest.raises(Exception):  # noqa: B017 — pydantic raises its own frozen-instance error
        settings.environment = "staging"  # type: ignore[misc]


def test_unrelated_env_vars_do_not_break_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray, unrelated env var never leaks into settings or crashes loading (extra='ignore')."""
    monkeypatch.setenv("SOME_UNRELATED_VAR", "should-not-appear")

    settings = Settings()

    assert not hasattr(settings, "some_unrelated_var")


def test_jwt_leeway_seconds_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDITMIND_JWT_LEEWAY_SECONDS", "9999")

    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError, out of the le=300 bound
        Settings()
