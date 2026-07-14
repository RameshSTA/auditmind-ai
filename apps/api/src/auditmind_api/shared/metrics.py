"""Business (not auto-instrumented request/DB) metrics — signals no generic HTTP/DB instrumentation
produces on its own: how many documents got ingested, findings created, anomalies detected, risk
scores computed.

Lives in ``shared/`` rather than any one bounded context's own module for the same reason
``shared/logging.py`` does: every context needs to increment these, and no single context owns
"how many findings exist platform-wide" as its own concern more than any other. Counters are
created at import time (module-level), before ``configure_telemetry`` runs — safe because OTel's
``get_meter()`` returns a proxy that defers to the real ``MeterProvider`` once
``configure_telemetry`` sets it.

Incremented from ``main.py`` (the composition root), not from inside a bounded context's own
service: a metric counting something is cross-cutting observability, not business logic a context
needs to know about itself.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("auditmind_api")

documents_ingested_total = _meter.create_counter(
    "auditmind.documents_ingested_total",
    unit="1",
    description="Documents successfully ingested.",
)
findings_created_total = _meter.create_counter(
    "auditmind.findings_created_total",
    unit="1",
    description="Draft findings created.",
)
anomalies_detected_total = _meter.create_counter(
    "auditmind.anomalies_detected_total",
    unit="1",
    description="Anomalies newly flagged by the rule engine.",
)
risk_scores_computed_total = _meter.create_counter(
    "auditmind.risk_scores_computed_total",
    unit="1",
    description="Transaction risk scores newly recorded by the fraud-scoring ensemble.",
)
