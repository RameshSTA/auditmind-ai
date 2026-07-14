"""OpenTelemetry setup.

Traces and metrics are pushed via OTLP to the Collector — the single fan-out point every service
reports to. This app never talks to Prometheus, Langfuse, or a log sink directly: without the
Collector, every service would need to know about Prometheus, Langfuse, and the log sink
individually; the Collector centralizes that knowledge once.

Auto-instrumentation (FastAPI, SQLAlchemy, httpx) covers the generic request/DB-call layer with no
manual span code. Manual spans are reserved for LangGraph node boundaries in
``services/agent-orchestrator``, not this app.

**Two entry points, deliberately split, not one — verified this matters, not a style preference.**
``instrument_app`` (FastAPI-specific middleware installation) must run immediately after
constructing the ``FastAPI()`` instance, before any ``@app.middleware`` decorator is registered —
Starlette nests middleware in registration order, so OTel's span-creating middleware has to be
registered first to end up *outermost*; confirmed directly (not assumed) by testing that the
`x-trace-id` response header came back as a fallback uuid4, not a real OTel trace id, until this
ordering was fixed. ``configure_providers`` (the actual ``TracerProvider``/``MeterProvider``
construction, each with a live background export thread) is deliberately deferred to the FastAPI
*lifespan* startup, not called from ``instrument_app`` at construction time — ``main.py`` keeps a
module-level ``app = create_app()`` for uvicorn's entrypoint string, which is constructed on every
import (including every test file that imports anything from ``main``) but whose lifespan is only
ever entered by a real running server or a test's own ``with TestClient(app)``. Constructing real
providers at import time would spin up a live background thread for every such import that's never
actually used, orphaned for the rest of the process — confirmed directly: doing this once produced
after-teardown "Logging error" tracebacks from a background export thread retrying against streams
pytest had already closed, across an entire test session's worth of unused module-level app
instances. Deferring provider construction to lifespan startup means only apps that actually run
create one.
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from auditmind_api.shared.settings import Settings

_SERVICE_NAME = "auditmind-api"

# Bounds the per-RPC export timeout to the Collector — deliberately short. Verified directly
# (not assumed from docs) that the SDK default has a real cost: a `shutdown()` call with no
# Collector reachable took ~7.4s (gRPC connection retries) before this was set, and 0.014s after
# — a difference that would have added seconds to every test that builds and tears down an app.
# Production Collector calls are typically sub-millisecond on a local network; 1s is generous
# there and protects against exactly the dev/test scenario above.
_EXPORT_TIMEOUT_SECONDS = 1


def instrument_app(app: FastAPI) -> None:
    """Installs OTel's ASGI middleware on this specific app instance. Call immediately after
    constructing ``FastAPI(...)``, before registering any ``@app.middleware`` — see this module's
    own docstring for why the ordering is load-bearing, not cosmetic. Safe to call before
    ``configure_providers`` runs: the tracer this installs is an OTel ``ProxyTracer`` that
    transparently starts routing to the real provider once one is set (verified directly: a span
    created against the proxy before a real provider exists is silently dropped, never errors; a
    span created after ``configure_providers`` runs is correctly captured) — exactly the deferred-
    binding behavior this two-step split relies on.
    """
    FastAPIInstrumentor.instrument_app(app)


def configure_providers(settings: Settings) -> tuple[TracerProvider, MeterProvider]:
    """Constructs the real ``TracerProvider``/``MeterProvider`` (each with a live background
    export thread) and sets them as OTel's process-wide active providers. Call from the FastAPI
    lifespan's startup — see this module's own docstring for why deferring to here (not
    ``instrument_app``/``create_app``) matters.

    Safe to call when the Collector isn't reachable — the OTLP exporters retry/batch in the
    background and never block the request path on export failures; a dev machine running the API
    without ``docker compose``'s ``otel-collector`` service simply accumulates (and eventually
    drops) unsent batches rather than failing requests. Returns both providers so the caller can
    shut them down gracefully (``shutdown_telemetry``) without this module holding process-wide
    mutable state of its own.
    """
    resource = Resource.create(
        {SERVICE_NAME: _SERVICE_NAME, "deployment.environment": settings.environment}
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.otel_exporter_endpoint,
                insecure=True,
                timeout=_EXPORT_TIMEOUT_SECONDS,
            ),
            export_timeout_millis=_EXPORT_TIMEOUT_SECONDS * 1000,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=settings.otel_exporter_endpoint,
            insecure=True,
            timeout=_EXPORT_TIMEOUT_SECONDS,
        ),
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Process-wide, not per-app — idempotent-with-a-warning if called more than once per process
    # (every test that builds an app calls this once), the same "safe but noisy" characteristic
    # `set_tracer_provider`/`set_meter_provider` themselves already have.
    SQLAlchemyInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()

    return tracer_provider, meter_provider


def shutdown_telemetry(tracer_provider: TracerProvider, meter_provider: MeterProvider) -> None:
    """Flushes any queued spans/metrics before process exit — bounded to
    ``_EXPORT_TIMEOUT_SECONDS`` per that constant's own docstring, not an unbounded wait."""
    tracer_provider.shutdown()
    meter_provider.shutdown()


def current_trace_id() -> str | None:
    """The active span's trace id as a hex string, or ``None`` if no span is active — what
    ``main.py``'s request middleware binds into every structlog line so "a log line and its OTel
    trace are always joinable", replacing the request-local ``uuid4()`` that property only
    accidentally satisfied before (same value per request, but no relationship to the actual
    distributed trace)."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")
