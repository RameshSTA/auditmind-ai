"""Async SQLAlchemy engine, per-request session, and the Row-Level Security context helper
(Phase 4 §12, Phase 11 §2 Layer 3).

The engine connects as the least-privilege ``auditmind_app`` role — never as the migration/admin
role and never as a Postgres superuser — because RLS policies are silently bypassed for
superusers and table owners. If this ever connected as the owning/admin role, every RLS policy
in the schema would appear to work in testing while doing nothing in reality.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auditmind_api.shared.settings import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide async engine singleton.

    A module-level cache rather than ``lru_cache`` — tests that need an isolated engine (e.g.
    pointed at a different database) construct one directly via ``create_async_engine`` instead
    of relying on this shared instance.
    """
    global _engine, _session_factory
    if _engine is None:
        resolved_settings = settings or get_settings()
        _engine = create_async_engine(resolved_settings.database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None  # set by get_engine() immediately above
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request.

    Commits on success, rolls back on any exception — no route handler or application service is
    responsible for remembering to do either itself.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def set_rls_user_context(session: AsyncSession, *, user_id: str) -> None:
    """Binds ``app.current_user_id`` for the remainder of the current transaction only.

    Every RLS policy in this schema (starting with ``identity.engagement_members``, Phase 4 §12)
    is written against this session variable, so this call is what makes the database itself — not
    application code — the thing that actually enforces "a user can only see their own rows."

    Uses ``set_config(..., true)`` rather than ``SET LOCAL app.current_user_id = <value>`` for a
    specific reason: PostgreSQL's ``SET`` statement does not support bind parameters (it is parsed
    before parameter binding happens), so setting it directly would require string-interpolating
    ``user_id`` into the SQL text — exactly the kind of raw string construction this codebase
    otherwise refuses to do anywhere (Phase 5 §3's parameterized-template-only rule, applied here).
    ``set_config`` is a normal SQL function and accepts a bound parameter safely; its third
    argument (``true``) makes the setting transaction-local, equivalent to ``SET LOCAL``.
    """
    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": user_id},
    )
