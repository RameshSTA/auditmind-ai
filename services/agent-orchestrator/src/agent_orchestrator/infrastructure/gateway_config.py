"""Loads the LiteLLM gateway config from ``config/litellm.yaml`` (ADR-005).

The gateway config is a YAML file, not inlined Python — a model or provider change is a config
edit, never a code change. This module is the one place that reads it, returning a plain dict
:class:`~agent_orchestrator.infrastructure.llm_client.LiteLlmGatewayClient` indexes by alias.

Parsed with the stdlib-adjacent ``yaml`` loader that already ships as a transitive dependency of
``litellm``/``langchain`` — no new top-level dependency taken on just to read one config file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_orchestrator.shared.errors import ValidationError


def load_gateway_config(config_path: str) -> dict[str, Any]:
    """Read and parse the LiteLLM router config at ``config_path``.

    Raises :class:`ValidationError` (422) if the file is missing or does not contain a
    ``model_list`` — a misconfiguration that should fail fast and loud at startup, not surface as a
    confusing ``KeyError`` on the first model call. The presence of the file is a deployment
    concern; its *contents* being valid is this loader's job to check.
    """
    import yaml

    path = Path(config_path)
    if not path.is_file():
        raise ValidationError(
            f"LiteLLM gateway config not found at {config_path!r}. This service cannot route model "
            "calls without it. Set AGENT_LITELLM_CONFIG_PATH or place the file."
        )

    with path.open("r", encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)

    if not isinstance(parsed, dict) or "model_list" not in parsed:
        raise ValidationError(
            f"LiteLLM gateway config at {config_path!r} has no 'model_list' — nothing to route to."
        )

    return parsed
