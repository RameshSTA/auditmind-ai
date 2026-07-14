"""Unit tests for Settings — defaults, the AGENT_ env prefix, and the API-key presence check."""

from __future__ import annotations

import pytest

from agent_orchestrator.shared.settings import Settings


def test_default_environment_is_dev() -> None:
    assert Settings().environment == "dev"


def test_env_prefix_is_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DATABASE_HOST", "postgres")
    assert Settings().database_host == "postgres"


def test_database_url_uses_asyncpg_driver() -> None:
    settings = Settings(
        database_host="db",
        database_port=5432,
        database_name="auditmind",
        database_app_user="auditmind_app",
        database_app_password="secret",
    )
    assert settings.database_url == "postgresql+asyncpg://auditmind_app:secret@db:5432/auditmind"


def test_llm_provider_configured_false_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    assert Settings().llm_provider_configured is False


def test_llm_provider_configured_true_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LLM_API_KEY", "sk-test-not-real")
    assert Settings().llm_provider_configured is True
