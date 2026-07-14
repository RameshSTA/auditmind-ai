"""Reporting-specific exceptions, built on the shared RFC 7807 error hierarchy (Increment 01 /
Phase 3 §5). Lives in the application layer, not domain — the same convention ``ingestion``'s
exceptions module already established."""

from __future__ import annotations

from fastapi import status

from auditmind_api.shared.errors import AuditMindError


class InvalidFindingTransitionError(AuditMindError):
    """Raised when confirming or rejecting a finding that isn't currently ``draft``.

    A finding is disposed exactly once (Phase 1 FR-7.1's sign-off gate); re-confirming an already
    confirmed finding, or rejecting an already rejected one, is a client bug — a stale UI acting on
    a finding someone else has already dispositioned — not a state the API silently accepts.
    """

    type_slug = "invalid-finding-transition"
    title = "Finding is not awaiting disposition"
    status_code = status.HTTP_409_CONFLICT


class MissingDispositionReasonError(AuditMindError):
    """Rejecting a finding without a documented reason (US-06: "reject ... with a documented
    reason, so that professional judgment stays in control") is not a valid request."""

    type_slug = "missing-disposition-reason"
    title = "A disposition reason is required to reject a finding"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class ChunkNotFoundError(AuditMindError):
    type_slug = "chunk-not-found"
    title = "Cited chunk not found"
    status_code = status.HTTP_404_NOT_FOUND


class ChunkEngagementMismatchError(AuditMindError):
    """The cited chunk exists but belongs to a different engagement than the finding — see
    ``ChunkLookup``'s docstring in ``domain/ports.py`` for why this check exists at all."""

    type_slug = "chunk-engagement-mismatch"
    title = "Cited chunk does not belong to this engagement"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
