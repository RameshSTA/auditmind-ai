"""HTTP routes for the identity bounded context — self-service auth, caller identity, and
engagement membership/roster reads. Moved out of ``main.py`` (Phase 1 of the "decouple main.py"
increment) so this context's routes live alongside its own ``dependencies.py``/``schemas.py``,
matching every other layer of this context's hexagonal split."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.identity.application.services import IdentityService
from auditmind_api.identity.domain.entities import EngagementMembership, EngagementRosterEntry, User
from auditmind_api.identity.interface.dependencies import (
    get_current_db_user,
    get_identity_service,
    require_engagement_member,
)
from auditmind_api.identity.interface.schemas import (
    AuthIdentityResponse,
    LoginRequest,
    RegisterRequest,
)
from auditmind_api.shared.auth import AuthenticatedUser, get_current_user
from auditmind_api.shared.database import get_db_session, set_rls_user_context
from auditmind_api.shared.errors import NotFoundError
from auditmind_api.shared.settings import Settings, get_settings

router = APIRouter(tags=["identity"])


def _roster_entry_response(entry: EngagementRosterEntry) -> dict[str, object]:
    return {
        "user_id": entry.user_id,
        "display_name": entry.display_name,
        "email": entry.email,
        "role": entry.role.value,
        "granted_at": entry.granted_at.isoformat(),
    }


@router.post("/v1/auth/register", status_code=201)
async def register(
    body: RegisterRequest,
    identity_service: IdentityService = Depends(get_identity_service),
) -> AuthIdentityResponse:
    """Self-service signup: creates a real account (``identity.users`` + a bcrypt-hashed
    ``identity.credentials`` row) and auto-joins the demo engagement with the chosen role.

    Deliberately unauthenticated (no ``Depends(get_current_user)``) — this *is* the entry
    point that creates the identity a later request would authenticate as. The caller (the
    Next.js BFF) uses the returned ``subject`` to mint this identity's own session token,
    exactly as it would for a validated Entra identity; this API never mints tokens itself.
    """
    user, membership = await identity_service.register(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        role=body.role,
    )
    return AuthIdentityResponse(
        subject=user.entra_object_id,
        display_name=user.display_name,
        email=user.email,
        engagement_id=membership.engagement_id,
        role=membership.role.value,
    )


@router.post("/v1/auth/login")
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
    identity_service: IdentityService = Depends(get_identity_service),
    settings: Settings = Depends(get_settings),
) -> AuthIdentityResponse:
    """Verifies email/password against ``identity.credentials`` and returns the identity for
    the BFF to mint a session token for — see ``register`` above for why this API never mints
    tokens itself."""
    user = await identity_service.authenticate(email=body.email, password=body.password)
    # Binds RLS before the membership lookup below — same requirement as every other
    # RLS-protected read (Phase 4 §12): without this, the row exists but is invisible.
    await set_rls_user_context(session, user_id=user.id)
    membership = await identity_service.get_membership(
        user_id=user.id, engagement_id=settings.default_engagement_id
    )
    return AuthIdentityResponse(
        subject=user.entra_object_id,
        display_name=user.display_name,
        email=user.email,
        engagement_id=membership.engagement_id if membership else None,
        role=membership.role.value if membership else None,
    )


@router.get("/v1/me")
async def whoami(user: AuthenticatedUser = Depends(get_current_user)) -> dict[str, object]:
    """Returns the caller's validated identity.

    Useful as an end-to-end smoke test for the auth middleware, and as the first concrete
    example other bounded contexts follow when they need the current user.
    """
    return {
        "subject": user.subject,
        "roles": sorted(user.roles),
        "tenant_id": user.tenant_id,
    }


@router.get("/v1/me/engagements")
async def my_engagements(
    db_user: User = Depends(get_current_db_user),
    identity_service: IdentityService = Depends(get_identity_service),
) -> list[dict[str, str]]:
    """Lists the caller's own engagement memberships, each carrying the engagement's real name —
    so the frontend never has to fall back to displaying a bare id.

    Returns only the caller's own rows purely because Postgres RLS enforces it (Phase 4 §12)
    — the query issued here has no ``WHERE user_id = ...`` clause of its own to get wrong.
    """
    engagements = await identity_service.list_current_user_engagements()
    return [
        {"engagement_id": e.engagement_id, "name": e.name, "role": e.role.value}
        for e in engagements
    ]


@router.get("/v1/engagements/{engagement_id}/membership")
async def my_engagement_membership(
    membership: EngagementMembership = Depends(require_engagement_member()),
) -> dict[str, str]:
    """Returns the caller's role on a specific engagement, or a 403 if they aren't a member —
    the concrete end-to-end proof of Phase 11 §4's "always re-check against the database"
    decision."""
    return {"engagement_id": membership.engagement_id, "role": membership.role.value}


@router.get("/v1/engagements/{engagement_id}")
async def get_engagement(
    engagement_id: str,
    _membership: EngagementMembership = Depends(require_engagement_member()),
    identity_service: IdentityService = Depends(get_identity_service),
) -> dict[str, str]:
    """This engagement's real name (and the caller's role on it) — the breadcrumb and any other
    surface that would otherwise have nothing but a bare id to show."""
    engagement = await identity_service.get_current_user_engagement(engagement_id)
    if engagement is None:
        raise NotFoundError(f"Engagement {engagement_id} not found.")
    return {
        "engagement_id": engagement.engagement_id,
        "name": engagement.name,
        "role": engagement.role.value,
    }


@router.get("/v1/engagements/{engagement_id}/members")
async def list_engagement_members(
    membership: EngagementMembership = Depends(require_engagement_member()),
    identity_service: IdentityService = Depends(get_identity_service),
) -> list[dict[str, object]]:
    """Administration's roster view — every member of this engagement and their role. Any
    member may call this (same "any member" gate as the membership/evidence/findings routes),
    not just Admin/CAE: seeing who else has access to an engagement you're already in isn't a
    privileged action, it's the same visibility a shared workspace's member list always has.
    The ``engagement_members_engagement_roster`` RLS policy is what actually enforces that a
    non-member sees nothing, regardless of what this route's own membership check does."""
    roster = await identity_service.list_engagement_roster(membership.engagement_id)
    return [_roster_entry_response(entry) for entry in roster]
