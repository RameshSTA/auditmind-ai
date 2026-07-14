"""Application configuration for the Agent Orchestration service.

Every setting is read from environment variables only (Phase 3 §4) — never hardcoded, never read
from an ad hoc config file. This mirrors ``apps/api``'s ``shared/settings.py`` exactly (same
``AGENT_`` prefix convention, same frozen singleton), because this service holds itself to the same
configuration discipline even though it is a separate deployable (ADR-001).

One value is deliberately *not* modelled here: ``ANTHROPIC_API_KEY``. LiteLLM reads that variable
directly from the process environment when it makes a model call (Phase 2 ADR-005 — the gateway
owns provider credentials, not application code), so surfacing it through ``Settings`` would be a
second, redundant source of truth for a secret. ``llm_provider_configured`` below reports whether
it is present without this module ever reading, storing, or logging the key itself.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration, prefixed ``AGENT_`` in the environment.

    Example: ``AGENT_DATABASE_HOST=postgres`` sets ``database_host``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    environment: str = Field(
        default="dev",
        description="Deployment environment: dev | staging | prod (Phase 3 §9 / Phase 12).",
    )
    log_level: str = Field(default="INFO", description="Python logging level name.")

    # --- Entra ID / OIDC (Phase 2 §7, Phase 11 §1/§4/§5) ---
    # A caller reaching this service already authenticated once against apps/api's identity
    # context (Increment 02) — this is the same platform-wide Entra tenant, validated the same
    # way, deliberately not a second identity provider. Kept as this service's own settings
    # (``AGENT_ENTRA_*``, not imported from apps/api) because the two are separate deployables
    # (ADR-001) that must be independently configurable and independently deployable.
    entra_tenant_id: str = Field(default="")
    entra_client_id: str = Field(default="", description="This service's expected JWT audience.")
    entra_issuer: str = Field(default="")
    entra_jwks_uri: str = Field(default="")
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)

    # --- Database (Phase 4 §1 agent schema, Phase 11 §7/§8) ---
    # This service owns the `agent` schema (agent.runs / checkpoints / hitl_interrupts) but shares
    # the same physical Postgres instance as apps/api — the two are one database with several
    # schemas, not two databases (Phase 4 §1 lists every schema, `agent` among them, under one
    # data layer). It connects as the same least-privilege `auditmind_app` role, which this
    # service's own migration grants access to the `agent` schema.
    database_host: str = Field(default="localhost")
    database_port: int = Field(default=5432)
    database_name: str = Field(default="auditmind")
    database_app_user: str = Field(
        default="auditmind_app",
        description="Least-privilege role the service connects as — never the migration/admin "
        "role, so RLS policies on agent.* actually apply (Phase 4 §12).",
    )
    database_app_password: str = Field(default="")

    # --- LiteLLM gateway / model routing (Phase 2 ADR-005, Phase 5 §12) ---
    litellm_config_path: str = Field(
        default="./config/litellm.yaml",
        description="Path to the LiteLLM router config (config/litellm.yaml) — the declarative "
        "model list and routing policy. Read at startup by the gateway adapter, never inlined in "
        "code, so a model or provider change is a config edit, not a code change (ADR-005).",
    )
    default_model: str = Field(
        default="claude-primary",
        description="The LiteLLM model *alias* (a name from litellm.yaml's model_list, not a raw "
        "provider model id) that agent nodes route to unless a node overrides it. Points at a "
        "Claude model via the gateway — the provider decision made for this project (ADR-005).",
    )

    # --- RAG tool client (Increment 12's named gap, closed here) ---
    core_api_base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of apps/api — where the RAG tool client (infrastructure/"
        "api_client.py) sends hybrid_search/submit_finding_draft calls, forwarding the caller's "
        "own bearer token so apps/api's RLS/engagement-membership checks are what actually "
        "enforce access, same as every other caller of that API.",
    )

    # --- Graph execution bounds (Phase 5 §14/§17) ---
    max_replans: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Bounded re-plan ceiling (Phase 5 §14): after this many Evaluation-driven "
        "re-plans, the run terminates as 'cannot conclude' (AC-01) rather than looping forever.",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def database_url(self) -> str:
        """An asyncpg-driver SQLAlchemy URL for the least-privilege application role."""
        return (
            f"postgresql+asyncpg://{self.database_app_user}:{self.database_app_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def llm_provider_configured(self) -> bool:
        """Whether an LLM provider API key is present in the environment for LiteLLM to use.

        Reads ``OPENAI_API_KEY`` presence only — never its value — so this can be logged and
        returned from a health endpoint safely. ADR-005 defaults to Anthropic's Claude; this
        environment runs on OpenAI instead (see config/litellm.yaml's header for the swap), so
        this checks the credential actually in use here rather than the ADR's default provider.
        When ``False``, the graph still builds and every non-LLM node still runs; the first node
        that actually calls a model raises a clear, typed ``LlmProviderNotConfiguredError`` (see
        infrastructure/llm_client.py) instead of a raw provider authentication error.
        """
        return bool(os.environ.get("OPENAI_API_KEY"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings singleton.

    Cached because ``Settings()`` re-reads and re-validates every environment variable on
    construction — paid once per process, not once per request. Tests that need different settings
    construct ``Settings(...)`` directly, or call ``get_settings.cache_clear()`` first.
    """
    return Settings()
