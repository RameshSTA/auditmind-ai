"""RFC 7807 (``application/problem+json``) error envelope (Phase 3 §5).

Same contract as ``apps/api``'s ``shared/errors.py``: domain/application code raises a typed
subclass of :class:`AgentOrchestratorError`; the exception handlers registered here are the only
place that turns an exception into an HTTP response. Application and domain code never construct a
``JSONResponse`` or reach for an HTTP status code directly.

The one addition specific to this service is :class:`LlmProviderNotConfiguredError` (503) — the
typed error every LLM-calling node raises when the configured provider's API key (``OPENAI_API_KEY``
in this environment; ``ANTHROPIC_API_KEY`` under ADR-005's default) is not present, so a run that
reaches a model call in an unconfigured environment fails with a clear, documented 503 rather than
a raw provider authentication traceback (see the module docstring in
``infrastructure/llm_client.py``).
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


class AgentOrchestratorError(Exception):
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


class NotFoundError(AgentOrchestratorError):
    type_slug = "not-found"
    title = "Resource not found"
    status_code = status.HTTP_404_NOT_FOUND


class AuthenticationError(AgentOrchestratorError):
    type_slug = "authentication-error"
    title = "Authentication required"
    status_code = status.HTTP_401_UNAUTHORIZED


class AuthorizationError(AgentOrchestratorError):
    type_slug = "authorization-error"
    title = "Access denied"
    status_code = status.HTTP_403_FORBIDDEN


class ValidationError(AgentOrchestratorError):
    type_slug = "validation-error"
    title = "Validation error"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class GuardrailViolationError(AgentOrchestratorError):
    """A hard Guardrail stop (Phase 5 §9/§17) — never retried, surfaced as a 422 to the caller and
    (in a fuller build) alerted to governance. Distinct from a validation error so a guardrail
    event is filterable in the logs as its own class."""

    type_slug = "guardrail-violation"
    title = "Guardrail violation"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class UpstreamApiError(AgentOrchestratorError):
    """apps/api rejected or could not be reached for a RAG tool call
    (``infrastructure/api_client.py`` — ``hybrid_search``/``submit_finding_draft``). 502 (Bad
    Gateway): this service is healthy, the caller's request was valid, but the upstream it depends
    on for real evidence failed — distinct from this service's own validation/auth errors."""

    type_slug = "upstream-api-error"
    title = "Upstream API error"
    status_code = status.HTTP_502_BAD_GATEWAY


class LlmProviderNotConfiguredError(AgentOrchestratorError):
    """No provider API key is configured, so the gateway cannot make a model call.

    503 (Service Unavailable) rather than 500: the service itself is healthy and correctly wired —
    it is a required piece of *configuration* that is absent, exactly the condition 503 exists for.
    This is the error the whole 'built but unverifiable without a key' boundary funnels through.
    """

    type_slug = "llm-provider-not-configured"
    title = "LLM provider not configured"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


def _trace_id_from(request: Request) -> str:
    return str(getattr(request.state, "trace_id", None) or uuid.uuid4())


async def agent_orchestrator_error_handler(
    request: Request, exc: AgentOrchestratorError
) -> JSONResponse:
    """Maps any :class:`AgentOrchestratorError` to its RFC 7807 response.

    5xx-mapped exceptions are logged at ERROR (a real system problem); 4xx-mapped exceptions at
    INFO (an expected, handled client condition). A 503 configuration error is logged at WARNING —
    it is neither a client mistake nor a system fault, it is an operator/deployment gap worth
    surfacing more prominently than an INFO line but not as an ERROR page.
    """
    trace_id = _trace_id_from(request)

    if exc.status_code >= 500 and exc.status_code != status.HTTP_503_SERVICE_UNAVAILABLE:
        logger.error(
            "unhandled_domain_error", error_type=exc.type_slug, trace_id=trace_id, detail=exc.detail
        )
    elif exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        logger.warning(
            "service_unavailable", error_type=exc.type_slug, trace_id=trace_id, detail=exc.detail
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
    """Backstop for any exception that is *not* an :class:`AgentOrchestratorError`.

    Never leaks the original exception message or a stack trace to the client — only the trace id,
    which is what support/on-call use to find the real detail in the logs (Phase 10 §1).
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
