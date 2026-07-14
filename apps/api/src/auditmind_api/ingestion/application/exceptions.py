"""Ingestion-specific exceptions, built on the shared RFC 7807 error hierarchy. Lives in the
application layer, not domain — the domain layer stays framework-free; ``shared.errors`` imports
FastAPI, so anything built on it belongs at or above the application layer, the same place
``identity``'s services raise ``AuthorizationError`` from."""

from __future__ import annotations

from fastapi import status

from auditmind_api.shared.errors import AuditMindError


class UnsupportedMimeTypeError(AuditMindError):
    type_slug = "unsupported-mime-type"
    title = "Unsupported document type"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class DocumentParsingError(AuditMindError):
    type_slug = "document-parsing-failed"
    title = "Document could not be parsed"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
