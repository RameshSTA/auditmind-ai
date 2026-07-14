"""RFC 7807 (``application/problem+json``) error envelope.

Domain code raises a typed subclass of :class:`AuditMindError`; the exception handlers registered
here are the *only* place that knows how to turn an exception into an HTTP response. Application
and domain code never construct a ``JSONResponse`` or reach for an HTTP status code directly —
that coupling to HTTP belongs solely to this module and to FastAPI's routing layer.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

_PROBLEM_BASE_URI = "https://auditmind.ai/errors"


class ProblemDetail(BaseModel):
    """The RFC 7807 response body shape."""

    type: str
    title: str
    status: int
    detail: str
    trace_id: str


class AuditMindError(Exception):
    """Base class for every domain exception raised anywhere in this service.

    Never raised directly — always through one of its subclasses below, each of which fixes the
    ``type_slug``, ``title``, and HTTP status that subclass maps to.
    """

    type_slug: str = "internal-error"
    title: str = "Internal error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(AuditMindError):
    type_slug = "not-found"
    title = "Resource not found"
    status_code = status.HTTP_404_NOT_FOUND


class ValidationError(AuditMindError):
    type_slug = "validation-error"
    title = "Validation error"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class AuthenticationError(AuditMindError):
    type_slug = "authentication-error"
    title = "Authentication required"
    status_code = status.HTTP_401_UNAUTHORIZED


class AuthorizationError(AuditMindError):
    type_slug = "authorization-error"
    title = "Access denied"
    status_code = status.HTTP_403_FORBIDDEN


def _trace_id_from(request: Request) -> str:
    return str(getattr(request.state, "trace_id", None) or uuid.uuid4())


async def auditmind_error_handler(request: Request, exc: AuditMindError) -> JSONResponse:
    """Maps any :class:`AuditMindError` to its RFC 7807 response.

    5xx-mapped exceptions are logged at ERROR (they represent a real system problem); 4xx-mapped
    exceptions are logged at INFO (they represent an expected, handled client condition — a bad
    request is not a system failure).
    """
    trace_id = _trace_id_from(request)

    if exc.status_code >= 500:
        logger.error(
            "unhandled_domain_error", error_type=exc.type_slug, trace_id=trace_id, detail=exc.detail
        )
    else:
        logger.info(
            "handled_domain_error", error_type=exc.type_slug, trace_id=trace_id, detail=exc.detail
        )

    problem = ProblemDetail(
        type=f"{_PROBLEM_BASE_URI}/{exc.type_slug}",
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(),
        media_type="application/problem+json",
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Backstop for any exception that is *not* an :class:`AuditMindError`.

    Never leaks the original exception message or a stack trace to the client — only the trace id,
    which is what support/on-call use to find the real detail in the logs.
    """
    trace_id = _trace_id_from(request)
    logger.error("unhandled_exception", trace_id=trace_id, exc_info=exc)

    problem = ProblemDetail(
        type=f"{_PROBLEM_BASE_URI}/internal-error",
        title="Internal error",
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred. Reference the trace id when contacting support.",
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=problem.model_dump(),
        media_type="application/problem+json",
    )
