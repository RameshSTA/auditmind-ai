"""Application configuration.

Every setting is read from environment variables only — never hardcoded, never read from an ad hoc
config file. This is the single place a service reaches for configuration; nothing else in the
codebase should call ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration, prefixed ``AUDITMIND_`` in the environment.

    Example: ``AUDITMIND_ENTRA_TENANT_ID=...`` sets ``entra_tenant_id``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUDITMIND_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    environment: str = Field(
        default="dev",
        description="Deployment environment: dev | staging | prod.",
    )
    log_level: str = Field(default="INFO", description="Python logging level name.")

    # --- Entra ID / OIDC ---
    entra_tenant_id: str = Field(
        default="",
        description="Azure Entra ID tenant id, used to derive the OIDC authority.",
    )
    entra_client_id: str = Field(
        default="",
        description="This API's app registration client id — the expected JWT audience.",
    )
    entra_issuer: str = Field(
        default="",
        description="Expected JWT issuer, e.g. https://login.microsoftonline.com/{tenant}/v2.0",
    )
    entra_jwks_uri: str = Field(
        default="",
        description="JWKS endpoint used to fetch signing keys for token validation.",
    )
    jwt_leeway_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        description="Clock-skew tolerance applied to exp/nbf validation.",
    )

    # --- Database ---
    database_host: str = Field(default="localhost")
    database_port: int = Field(default=5432)
    database_name: str = Field(default="auditmind")
    database_app_user: str = Field(
        default="auditmind_app",
        description="Least-privilege role the application connects as — never the migration/admin "
        "role, so RLS policies actually apply.",
    )
    database_app_password: str = Field(default="")

    # --- Knowledge Graph / Neo4j ---
    neo4j_uri: str = Field(
        default="bolt://localhost:7687",
        description="Bolt connection URI. Neo4j has no equivalent to Postgres's least-privilege "
        "app role + RLS story — every Cypher query this codebase issues must filter by "
        "engagement_id explicitly; see kg/infrastructure/neo4j_graph_store.py.",
    )
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="")

    # --- Observability ---
    otel_exporter_endpoint: str = Field(
        default="localhost:4317",
        description="OTLP gRPC endpoint of the Collector every service pushes traces/metrics to "
        "— never Prometheus or a log sink directly. Unreachable in dev without docker-compose's "
        "otel-collector service running; exporters batch/retry in the background rather than "
        "failing requests when it's unreachable.",
    )

    # --- Ingestion / blob storage ---
    blob_storage_root: str = Field(
        default="./data/blobs",
        description="Local filesystem root standing in for Azure Blob Storage — see "
        "ingestion/infrastructure/local_blob_storage.py. Replaced by an Azure adapter (same "
        "BlobStorage port, new infrastructure file) when that migration happens.",
    )
    max_upload_size_bytes: int = Field(
        default=25 * 1024 * 1024,  # 25 MB
        description="Rejects an upload before it is read into memory at all if the declared "
        "Content-Length exceeds this — a resource-exhaustion guard.",
    )

    # --- Identity / self-service signup ---
    default_engagement_id: str = Field(
        default="00000000-0000-0000-0000-0000000000e1",
        description="Engagement a self-service signup is auto-joined to (the same fixture "
        "engagement scripts/seed_dev.py seeds). Real member-invite/admin-add flows are a future "
        "addition; until then, self-service signup has exactly one engagement to join.",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings singleton.

    Cached because ``Settings()`` re-reads and re-validates every environment variable on
    construction — that cost should be paid once per process, not once per request. Tests that
    need different settings should construct ``Settings(...)`` directly rather than relying on
    this cache, or call ``get_settings.cache_clear()`` first.
    """
    return Settings()
