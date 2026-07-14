"""Request bodies for the Reporting context's routes.

The only Pydantic models in this codebase used for JSON request bodies rather than the RFC 7807
error envelope — kept in the interface layer, same as every other FastAPI-facing concern.
``main.py`` never accepts a raw ``dict`` body; validation happens before a route handler runs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from auditmind_api.reporting.domain.entities import FindingSeverity


class CreateFindingRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: FindingSeverity
    control_id: str | None = None


class AttachEvidenceRequest(BaseModel):
    chunk_id: str
    citation_text: str = Field(min_length=1)


class RejectFindingRequest(BaseModel):
    disposition_reason: str = Field(min_length=1)
