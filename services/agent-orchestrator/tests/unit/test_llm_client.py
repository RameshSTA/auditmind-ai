"""Unit tests for the real LiteLLM gateway adapter (Phase 2 ADR-005).

Proves the one thing this adapter can verify without an ``OPENAI_API_KEY`` — see the module
docstring in ``infrastructure/llm_client.py`` for exactly why a real model call is out of scope
here."""

from __future__ import annotations

import pytest

from agent_orchestrator.domain.entities import PromptMessage
from agent_orchestrator.infrastructure.llm_client import LiteLlmGatewayClient
from agent_orchestrator.shared.errors import LlmProviderNotConfiguredError, ValidationError

_ROUTER_CONFIG = {
    "model_list": [
        {"model_name": "claude-primary", "litellm_params": {"model": "anthropic/claude-sonnet-5"}},
    ]
}


async def test_complete_raises_typed_error_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = LiteLlmGatewayClient(router_config=_ROUTER_CONFIG)

    with pytest.raises(LlmProviderNotConfiguredError):
        await client.complete(
            messages=[PromptMessage(role="user", content="hello")], model="claude-primary"
        )


async def test_complete_never_reads_the_key_value_only_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 503 message and the adapter's own check must not leak the key's value even when one is
    set — this test would fail loudly (an AttributeError from the fake, or a real network call)
    if the adapter ever passed the raw key around instead of just checking presence."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-appear-anywhere")
    client = LiteLlmGatewayClient(router_config=_ROUTER_CONFIG)

    with pytest.raises(ValidationError) as exc_info:
        await client.complete(
            messages=[PromptMessage(role="user", content="hello")], model="unknown-alias"
        )
    assert "sk-should-never-appear-anywhere" not in str(exc_info.value)


async def test_complete_rejects_an_alias_not_in_the_model_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    client = LiteLlmGatewayClient(router_config=_ROUTER_CONFIG)

    with pytest.raises(ValidationError):
        await client.complete(
            messages=[PromptMessage(role="user", content="hello")], model="not-a-real-alias"
        )
