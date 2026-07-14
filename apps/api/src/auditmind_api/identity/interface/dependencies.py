"""FastAPI dependencies wiring the Identity context into the request lifecycle.

This is the only layer in the identity context allowed to import FastAPI (Phase 3 §1) — the
``interface/`` layer.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.identity.application.services import IdentityService
from auditmind_api.identity.domain.entities import EngagementMembership, EngagementRole, User
from auditmind_api.identity.infrastructure.repository import (
    PostgresCredentialRepository,
    PostgresEngagementMembershipRepository,
    PostgresUserRepository,
)
from auditmind_api.identity.infrastructure.security import (
    BcryptPasswordHasher,
    PostgresRlsContextBinder,
)
from auditmind_api.shared.auth import AuthenticatedUser, get_current_user
from auditmind_api.shared.database import get_db_session, set_rls_user_context
from auditmind_api.shared.settings import Settings, get_settings


def get_identity_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> IdentityService:
    return IdentityService(
        user_repository=PostgresUserRepository(session),
        membership_repository=PostgresEngagementMembershipRepository(session),
        credential_repository=PostgresCredentialRepository(session),
        password_hasher=BcryptPasswordHasher(),
        rls_context_binder=PostgresRlsContextBinder(session),
        default_engagement_id=settings.default_engagement_id,
    )


async def get_current_db_user(
    auth_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    identity_service: IdentityService = Depends(get_identity_service),
) -> User:
    """Resolves (or JIT-provisions) the internal ``identity.users`` row for the validated Entra
    identity, then binds the Row-Level Security context for every subsequent query in this
    request's transaction (Phase 4 §12).

    ``display_name``/``email`` are populated from the token subject as a placeholder — enriching
    them from Microsoft Graph is explicitly out of scope for this increment (see the increment
    doc). Using the subject here is an honest placeholder value, not a silent fabrication of data
    that looks real.
    """
    user = await identity_service.resolve_or_provision_user(
        entra_object_id=auth_user.subject,
        display_name=auth_user.subject,
        email="",
    )
    await set_rls_user_context(session, user_id=user.id)
    return user


def require_engagement_member(
    *allowed_roles: EngagementRole,
) -> Callable[..., Coroutine[Any, Any, EngagementMembership]]:
    """FastAPI dependency factory: the concrete implementation of Phase 11 §4's decision that
    engagement scope is always re-checked against the database, never trusted from the JWT.

    Usage: ``Depends(require_engagement_member())`` for "any member", or
    ``Depends(require_engagement_member(EngagementRole.AUDITOR, EngagementRole.CAE))`` to
    additionally restrict by role within the engagement. Relies on FastAPI's path-parameter
    injection — the route this is used on must declare ``{engagement_id}`` in its path.
    """

    async def _check(
        engagement_id: str,
        db_user: User = Depends(get_current_db_user),
        identity_service: IdentityService = Depends(get_identity_service),
    ) -> EngagementMembership:
        roles = frozenset(allowed_roles) if allowed_roles else None
        return await identity_service.require_membership(
            user_id=db_user.id, engagement_id=engagement_id, allowed_roles=roles
        )

    return _check
