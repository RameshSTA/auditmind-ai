"""Investigations-specific exceptions, built on the shared RFC 7807 error hierarchy (Increment 01 /
Phase 3 §5). Lives in the application layer, not domain — the same convention every prior context
established."""

from __future__ import annotations

from fastapi import status

from auditmind_api.shared.errors import AuditMindError


class InvestigationAlreadyClosedError(AuditMindError):
    """Raised when closing or adding an item to an investigation that's already closed — the same
    "no path backward" discipline ``reporting``'s ``InvalidFindingTransitionError`` establishes for
    finding disposition."""

    type_slug = "investigation-already-closed"
    title = "Investigation is already closed"
    status_code = status.HTTP_409_CONFLICT


class MissingConclusionError(AuditMindError):
    """Closing an investigation without a documented conclusion is not a valid request — the same
    "professional judgment stays in control" reasoning ``reporting``'s
    ``MissingDispositionReasonError`` establishes for rejecting a finding."""

    type_slug = "missing-conclusion"
    title = "A conclusion is required to close an investigation"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class InvalidSubjectTypeError(AuditMindError):
    type_slug = "invalid-subject-type"
    title = "Unrecognized investigation item subject type"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class SubjectNotFoundError(AuditMindError):
    type_slug = "subject-not-found"
    title = "Item subject not found"
    status_code = status.HTTP_404_NOT_FOUND


class SubjectEngagementMismatchError(AuditMindError):
    """The subject exists but belongs to a different engagement than the investigation — see
    ``InvestigationSubjectLookup``'s docstring in ``domain/ports.py`` for why this check exists at
    all."""

    type_slug = "subject-engagement-mismatch"
    title = "Item subject does not belong to this engagement"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
