"""Composition root.

This module's only job is wiring settings, logging, exception handlers, middleware, and routers
together — it contains no business logic and no route bodies of its own. Each bounded context
contributes an ``APIRouter`` from its own ``interface/router.py`` (route handlers that used to
live inline in this one file now live in their owning context's package); this file only lists
them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from auditmind_api.audit_trail.interface.router import router as audit_trail_router
from auditmind_api.identity.interface.router import router as identity_router
from auditmind_api.ingestion.interface.router import router as ingestion_router
from auditmind_api.investigations.interface.router import router as investigations_router
from auditmind_api.kg.interface.router import router as kg_router
from auditmind_api.reporting.interface.router import router as reporting_router
from auditmind_api.retrieval.interface.router import router as retrieval_router
from auditmind_api.risk.interface.router import router as risk_router
from auditmind_api.shared.auth import AuthenticatedUser, JWKSClient, require_role
from auditmind_api.shared.database import get_engine
from auditmind_api.shared.errors import (
    AuditMindError,
    auditmind_error_handler,
    unhandled_exception_handler,
)
from auditmind_api.shared.logging import configure_logging, get_logger
from auditmind_api.shared.neo4j import close_neo4j_driver
from auditmind_api.shared.settings import get_settings
from auditmind_api.shared.telemetry import (
    configure_providers,
    current_trace_id,
    instrument_app,
    shutdown_telemetry,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process startup/shutdown. Runs once per process, not per request or per test.

    ``configure_providers`` (not just ``instrument_app``, already called in ``create_app``) runs
    here deliberately — see ``shared/telemetry.py``'s own module docstring for why constructing
    the real providers (each with a live background export thread) has to wait for an app that's
    actually starting, not every app object that merely gets constructed (e.g. ``main.py``'s own
    module-level ``app = create_app()``, built on every import for uvicorn's entrypoint string but
    never actually run in most test processes).
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    tracer_provider, meter_provider = configure_providers(settings)
    app.state.jwks_client = JWKSClient(jwks_uri=settings.entra_jwks_uri)
    logger.info("startup_complete", environment=settings.environment)
    yield
    shutdown_telemetry(tracer_provider, meter_provider)
    await close_neo4j_driver()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    """Builds the FastAPI application. A function, not a module-level side effect, so tests can
    construct independent app instances with different lifespan/state where needed."""
    app = FastAPI(
        title="AuditMind AI — Core API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Must run here, immediately after construction and before any `@app.middleware` is
    # registered below — not deferred into `lifespan`. See `shared/telemetry.py`'s own module
    # docstring for why this specific call has to happen this early (Starlette middleware nesting
    # order) while the *provider* construction it depends on is deliberately deferred to
    # `lifespan` instead (avoiding a live background thread per module import).
    instrument_app(app)

    app.add_exception_handler(AuditMindError, auditmind_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.middleware("http")
    async def bind_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Binds a trace id to every log line emitted while handling this request and echoes it
        back in the response header so a client can quote it to support.

        Prefers the real OTel trace id (``current_trace_id()``) — the whole point of making "a
        log line and its OTel trace are always joinable" is defeated if this codebase's own
        request-scoped id and OTel's span id are two unrelated numbers. Falls back to a fresh
        uuid4 only if no span is active (confirmed this can happen: `FastAPIInstrumentor`'s ASGI
        span wraps *outside* this `@app.middleware` layer for the request as a whole, but a span
        is reliably active by the time this line runs — the fallback exists for defense, e.g. a
        test harness that never called `configure_telemetry`, not because it's expected in
        normal operation)."""
        trace_id = request.headers.get("x-trace-id") or current_trace_id() or str(uuid.uuid4())
        request.state.trace_id = trace_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id, path=request.url.path)
        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        return response

    @app.get("/healthz", tags=["health"])
    async def liveness() -> dict[str, str]:
        """Liveness probe: the process is up. Deliberately checks nothing else — a slow
        dependency should fail readiness, not cause Container Apps to restart a healthy
        process."""
        return {"status": "alive"}

    @app.get("/readyz", tags=["health"])
    async def readiness() -> JSONResponse:
        """Readiness probe: verifies the database is actually reachable, not just that the
        process started. A liveness-style "always 200" would have Container Apps keep routing
        traffic to a replica whose only database connection is down, which is exactly the failure
        readiness probes exist to catch (unlike ``/healthz``, which intentionally never checks
        downstream dependencies).
        """
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("readiness_check_failed", error=str(exc))
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.get("/v1/admin/ping", tags=["admin"])
    async def admin_ping(
        user: AuthenticatedUser = Depends(require_role("Admin")),
    ) -> dict[str, str]:
        """Demonstrates ``require_role`` end-to-end (RBAC) — a route only the Admin role may
        call. Kept here rather than moved into a context router: it isn't owned by any bounded
        context's business logic, it's a demonstration of a `shared/auth.py` mechanism."""
        return {"status": "ok", "subject": user.subject}

    app.include_router(identity_router)
    app.include_router(ingestion_router)
    app.include_router(reporting_router)
    app.include_router(risk_router)
    app.include_router(audit_trail_router)
    app.include_router(retrieval_router)
    app.include_router(kg_router)
    app.include_router(investigations_router)

    return app


app = create_app()
