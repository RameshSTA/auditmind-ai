"""Risk-specific exceptions, built on the shared RFC 7807 error hierarchy."""

from __future__ import annotations

from fastapi import status

from auditmind_api.shared.errors import AuditMindError


class InvalidAnomalyTransitionError(AuditMindError):
    """Raised when dispositioning an anomaly that isn't currently ``open`` — the same "disposed
    exactly once" discipline ``reporting.findings`` enforces, applied here to true/false-positive
    review."""

    type_slug = "invalid-anomaly-transition"
    title = "Anomaly is not awaiting disposition"
    status_code = status.HTTP_409_CONFLICT
