"""Unit tests for ``shared/telemetry.py`` — the pieces testable without a live Collector: trace id
extraction against a real (SDK, in-memory) span, and the "no active span" fallback case
``main.py``'s request middleware relies on.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider

from auditmind_api.shared.telemetry import current_trace_id


def test_current_trace_id_returns_none_with_no_active_span() -> None:
    assert current_trace_id() is None


def test_current_trace_id_returns_a_32_char_hex_string_for_an_active_span() -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("test-span"):
        trace_id = current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    assert all(c in "0123456789abcdef" for c in trace_id)
