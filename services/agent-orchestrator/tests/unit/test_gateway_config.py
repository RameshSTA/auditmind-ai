"""Unit tests for the LiteLLM gateway config loader (Phase 2 ADR-005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.infrastructure.gateway_config import load_gateway_config
from agent_orchestrator.shared.errors import ValidationError


def test_loads_the_real_committed_config() -> None:
    """The actual `config/litellm.yaml` this service ships — proves the loader and the shipped
    file agree, not just a loader tested against a synthetic fixture."""
    config_path = Path(__file__).parents[2] / "config" / "litellm.yaml"
    config = load_gateway_config(str(config_path))
    aliases = {entry["model_name"] for entry in config["model_list"]}
    assert "claude-primary" in aliases


def test_missing_file_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        load_gateway_config("/nonexistent/path/litellm.yaml")


def test_file_without_model_list_raises_validation_error(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("router_settings:\n  timeout: 60\n")
    with pytest.raises(ValidationError):
        load_gateway_config(str(bad_config))
