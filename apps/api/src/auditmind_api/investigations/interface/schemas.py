"""Request bodies for the Investigations context's routes — the interface layer, same convention
as every prior context's ``interface/schemas.py``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateInvestigationRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class AddInvestigationItemRequest(BaseModel):
    subject_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    note: str | None = None


class CloseInvestigationRequest(BaseModel):
    conclusion: str = Field(min_length=1)
