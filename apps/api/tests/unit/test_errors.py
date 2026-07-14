"""Unit tests for the RFC 7807 error envelope."""

from __future__ import annotations

import json

import pytest
from fastapi import status

from auditmind_api.shared.errors import (
    AuditMindError,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
    auditmind_error_handler,
    unhandled_exception_handler,
)


class _FakeRequestState:
    def __init__(self, trace_id: str | None) -> None:
        self.trace_id = trace_id


class _FakeRequest:
    """A minimal stand-in for fastapi.Request — only what the handlers actually read."""

    def __init__(self, trace_id: str | None = "trace-abc") -> None:
        self.state = _FakeRequestState(trace_id)


@pytest.mark.parametrize(
    ("error_cls", "expected_status", "expected_slug"),
    [
        (NotFoundError, status.HTTP_404_NOT_FOUND, "not-found"),
        (ValidationError, status.HTTP_422_UNPROCESSABLE_ENTITY, "validation-error"),
        (AuthenticationError, status.HTTP_401_UNAUTHORIZED, "authentication-error"),
        (AuthorizationError, status.HTTP_403_FORBIDDEN, "authorization-error"),
    ],
)
async def test_each_domain_error_maps_to_its_documented_status_and_slug(
    error_cls: type[AuditMindError], expected_status: int, expected_slug: str
) -> None:
    exc = error_cls("something specific went wrong")
    request = _FakeRequest()

    response = await auditmind_error_handler(request, exc)  # type: ignore[arg-type]
    body = json.loads(response.body)

    assert response.status_code == expected_status
    assert response.media_type == "application/problem+json"
    assert body["status"] == expected_status
    assert body["type"] == f"https://auditmind.ai/errors/{expected_slug}"
    assert body["detail"] == "something specific went wrong"
    assert body["trace_id"] == "trace-abc"


async def test_missing_trace_id_still_produces_a_valid_response() -> None:
    """If middleware somehow didn't run, a trace id is still generated rather than the response
    breaking or leaking an empty/None value to the client."""
    exc = NotFoundError("engagement not found")
    request = _FakeRequest(trace_id=None)

    response = await auditmind_error_handler(request, exc)  # type: ignore[arg-type]
    body = json.loads(response.body)

    assert body["trace_id"]  # non-empty
    assert body["trace_id"] != "None"


async def test_unhandled_exception_never_leaks_the_original_message() -> None:
    """A bare, unexpected exception must never have its message echoed back to the client —
    only a generic detail plus the trace id used to look up the real cause in the logs."""
    exc = RuntimeError("a database connection string with a password in it")
    request = _FakeRequest(trace_id="trace-xyz")

    response = await unhandled_exception_handler(request, exc)  # type: ignore[arg-type]
    body = json.loads(response.body)

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "password" not in body["detail"]
    assert "a database connection string" not in json.dumps(body)
    assert body["trace_id"] == "trace-xyz"
