"""Async SQLAlchemy engine, per-request session, and the Row-Level Security context helper.

Identical in shape and intent to ``apps/api``'s ``shared/database.py``: the engine connects as the
least-privilege ``auditmind_app`` role — never the migration/admin role, never a superuser —
because RLS policies are silently bypassed for superusers and table owners. The ``agent`` schema's
RLS policies (this service's migration) are written against the same ``app.current_user_id``
session variable the rest of the platform uses, so ``set_rls_user_context`` here is byte-for-byte
the same mechanism, pointed at this service's own tables.
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

from agent_orchestrator.shared.settings import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide async engine singleton.

    A module-level cache rather than ``lru_cache`` — tests that need an isolated engine (e.g.
    pointed at a different database) construct one directly via ``create_async_engine`` instead of
    relying on this shared instance.
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

    Every RLS policy on the ``agent`` schema is written against this session variable — the same
    one ``identity.engagement_members`` and every engagement-scoped table in ``apps/api`` uses — so
    this call is what makes the database itself enforce "a user can only touch runs on their own
    engagements."

    Uses ``set_config(..., true)`` rather than ``SET LOCAL`` because PostgreSQL's ``SET`` statement
    does not accept bind parameters, so setting the variable directly would require
    string-interpolating ``user_id`` into SQL text. ``set_config`` is a normal SQL function that
    binds safely; its third argument (``true``) makes the setting transaction-local, equivalent to
    ``SET LOCAL``.
    """
    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": user_id},
    )
