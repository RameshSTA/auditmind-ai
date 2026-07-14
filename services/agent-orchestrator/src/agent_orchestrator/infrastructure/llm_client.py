"""The real LiteLLM-gateway adapter for the :class:`LlmClient` port.

This is the concrete adapter every agent node's model call terminates at in production. It routes a
request through LiteLLM — never through a provider SDK directly — so the provider decision lives in
``config/litellm.yaml``'s model list, not in this code. Swapping providers, or adding a fallback
model, is a config edit behind this unchanged adapter.

**The exact line between built and unverifiable-without-a-key runs through this file.** Everything
above it in the stack — the graph, the routing, the reducers, the checkpointer, the repositories,
the HTTP interface — is built and verified against real infrastructure with a *fake* ``LlmClient``.
This adapter is the one component that cannot be verified against the real provider until an
``AGENT_LLM_API_KEY`` exists, because verifying it *is* making a real model call. What it *can* and
does verify without a key: that a missing key produces a clean, typed
:class:`LlmProviderNotConfiguredError` (503) rather than a raw provider traceback — see
``tests/unit/test_llm_client.py``.
"""

from __future__ import annotations

import os
from typing import Any

from agent_orchestrator.domain.entities import ModelResponse, PromptMessage
from agent_orchestrator.shared.errors import LlmProviderNotConfiguredError
from agent_orchestrator.shared.logging import get_logger

logger = get_logger(__name__)


class LiteLlmGatewayClient:
    """Routes model calls through the LiteLLM gateway configured in ``config/litellm.yaml``.

    Constructed with the parsed gateway config (a plain dict, loaded by ``gateway_config.py``) so
    the model-alias -> provider-model routing is data this adapter reads, not logic it hardcodes.
    The provider credential (``AGENT_LLM_API_KEY``) is read by LiteLLM itself from the environment
    at call time — this adapter never reads, stores, or logs the key, only checks its *presence*
    to fail fast with a clear error.
    """

    def __init__(self, *, router_config: dict[str, Any]) -> None:
        self._router_config = router_config
        # model alias -> concrete `litellm_params` (provider model id, etc.) from the config's
        # model_list. Pre-indexed once so each call is a dict lookup, not a linear scan.
        self._models_by_alias: dict[str, dict[str, Any]] = {
            entry["model_name"]: entry.get("litellm_params", {})
            for entry in router_config.get("model_list", [])
        }

    def _require_provider(self) -> None:
        if not os.environ.get("AGENT_LLM_API_KEY"):
            raise LlmProviderNotConfiguredError(
                "No AGENT_LLM_API_KEY is set in the environment. The Agent Orchestration service "
                "builds its graph and runs every non-LLM step without one, but a model call "
                "requires it. Set AGENT_LLM_API_KEY (see config/litellm.yaml for the configured "
                "provider) and retry."
            )

    async def complete(
        self, *, messages: list[PromptMessage], model: str, max_tokens: int = 1024
    ) -> ModelResponse:
        """Make one gateway completion call and return a provider-agnostic :class:`ModelResponse`.

        ``model`` is a LiteLLM alias from the config; it is resolved to its provider model id via
        the config's ``litellm_params`` so a node names ``"primary-model"``, never a raw provider
        model id. Raises :class:`LlmProviderNotConfiguredError` before any network call if no key
        is present.
        """
        self._require_provider()

        params = self._models_by_alias.get(model)
        if params is None:
            # A node asked for an alias the gateway config doesn't define — a wiring error, surfaced
            # as a validation error rather than silently falling back to some default model (which
            # would route audit reasoning to an unintended model, a governance problem).
            from agent_orchestrator.shared.errors import ValidationError

            raise ValidationError(
                f"Model alias {model!r} is not defined in the LiteLLM gateway config's model_list."
            )

        # Imported lazily so importing this module (and therefore the whole app) does not require
        # litellm to be importable in an environment that only ever uses the fake client — and so a
        # litellm import-time side effect never runs at app import.
        import litellm

        provider_model = params.get("model", model)
        response = await litellm.acompletion(
            model=provider_model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=choice.message.content or "",
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
