import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing each context's models module registers its tables onto the shared metadata below as
# a side effect of the import — SQLAlchemy tables only appear on `Base.metadata` once their
# module has actually executed. This import list grows as later increments add `risk`,
# `knowledge_graph`, etc.; each new context's models module is added here and nowhere else.
import auditmind_api.audit_trail.infrastructure.models  # noqa: F401
import auditmind_api.identity.infrastructure.models  # noqa: F401
import auditmind_api.ingestion.infrastructure.models  # noqa: F401
import auditmind_api.investigations.infrastructure.models  # noqa: F401
import auditmind_api.kg.infrastructure.models  # noqa: F401
import auditmind_api.reporting.infrastructure.models  # noqa: F401
import auditmind_api.retrieval.infrastructure.models  # noqa: F401
import auditmind_api.risk.infrastructure.models  # noqa: F401
from auditmind_api.shared.orm_base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate support: every bounded context's SQLAlchemy models register onto this shared
# metadata (the imports above).
target_metadata = Base.metadata

# Migrations run as a privileged/admin database role — never the least-privilege `auditmind_app`
# role the running application connects as (apps/api/src/auditmind_api/shared/database.py) —
# because creating schemas, roles, and RLS policies requires elevated grants the application
# itself must never hold. Read from its own env var, entirely separate from the app's
# `AUDITMIND_*` settings, so the admin credential is never accidentally wired into application
# `Settings`.
migration_url = os.environ.get("AUDITMIND_MIGRATION_DATABASE_URL")
if migration_url:
    config.set_main_option("sqlalchemy.url", migration_url)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # `include_schemas=True` is required the moment more than one non-`public` schema exists:
    # without it, autogenerate's reflection only sees the connection's default schema, so every
    # table in `identity`/`ingestion`/`reporting` looks "missing" and gets proposed for creation
    # again on every `--autogenerate` run. Autogenerate output must still always be reviewed by
    # hand; this only fixes reflection, not the general "never blindly accept autogenerate"
    # discipline.
    context.configure(
        connection=connection, target_metadata=target_metadata, include_schemas=True
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
