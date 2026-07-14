"""Request/response schemas for the identity context's public (unauthenticated) auth endpoints.

Kept separate from the domain entities they're built from — a request body is a wire-format
concern the domain layer never sees.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from auditmind_api.identity.application.services import SELF_SERVICE_ROLES
from auditmind_api.identity.domain.entities import EngagementRole

_SELF_SERVICE_ROLE_VALUES = sorted(role.value for role in SELF_SERVICE_ROLES)


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str = Field(..., min_length=1, max_length=200)
    role: EngagementRole

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "jane.doe@example.com",
                "password": "at-least-8-characters",
                "display_name": "Jane Doe",
                "role": _SELF_SERVICE_ROLE_VALUES[0],
            }
        }
    }


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)


class AuthIdentityResponse(BaseModel):
    """What a successful register/login call returns: enough for the caller (the Next.js BFF) to
    mint its own session token for this identity. This API never mints a token itself — see
    ``apps/web/src/server/dev-auth.ts``'s module docstring for why that stays the BFF's job."""

    subject: str
    display_name: str
    email: str
    engagement_id: str | None
    role: str | None
