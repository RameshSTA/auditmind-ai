"""Postgres-backed implementations of the identity repository ports (Phase 3 §1)."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.identity.domain.entities import (
    EngagementMembership,
    EngagementRole,
    EngagementRosterEntry,
    EngagementSummary,
    User,
)
from auditmind_api.identity.infrastructure.models import (
    CredentialModel,
    EngagementMembershipModel,
    EngagementModel,
    UserModel,
)


def _to_user_entity(model: UserModel) -> User:
    return User(
        id=str(model.id),
        entra_object_id=model.entra_object_id,
        display_name=model.display_name,
        email=model.email,
        created_at=model.created_at,
    )


def _to_membership_entity(model: EngagementMembershipModel) -> EngagementMembership:
    return EngagementMembership(
        engagement_id=str(model.engagement_id),
        user_id=str(model.user_id),
        role=EngagementRole(model.role),
        granted_at=model.granted_at,
    )


class PostgresUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_entra_object_id(self, entra_object_id: str) -> User | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.entra_object_id == entra_object_id)
        )
        model = result.scalar_one_or_none()
        return _to_user_entity(model) if model else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(UserModel).where(UserModel.email == email))
        model = result.scalar_one_or_none()
        return _to_user_entity(model) if model else None

    async def create(self, *, entra_object_id: str, display_name: str, email: str) -> User:
        model = UserModel(entra_object_id=entra_object_id, display_name=display_name, email=email)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_user_entity(model)


class PostgresCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: str, password_hash: str) -> None:
        self._session.add(CredentialModel(user_id=user_id, password_hash=password_hash))
        await self._session.flush()

    async def get_password_hash(self, user_id: str) -> str | None:
        result = await self._session.execute(
            select(CredentialModel.password_hash).where(CredentialModel.user_id == user_id)
        )
        return result.scalar_one_or_none()


class PostgresEngagementMembershipRepository:
    """Every query here runs under whatever RLS user context the caller has already bound via
    ``set_rls_user_context`` (``shared/database.py``), which every method here also relies on as a
    backstop — but as of the roster-visibility migration (``c8f21a3d5e9b``), RLS alone no longer
    scopes a bare `SELECT *` to "just my own row": the ``engagement_members_engagement_roster``
    policy legitimately returns every roster-mate's row too. ``list_for_current_user`` therefore
    now carries its own explicit ``WHERE user_id = current_setting('app.current_user_id')`` — still
    sourced from the session context, never a Python-supplied parameter (so it remains structurally
    impossible for a caller to query on behalf of a different user), just no longer relying on RLS
    to be the *only* thing narrowing "all memberships" down to "my memberships".
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_membership(
        self, *, user_id: str, engagement_id: str
    ) -> EngagementMembership | None:
        result = await self._session.execute(
            select(EngagementMembershipModel).where(
                EngagementMembershipModel.user_id == user_id,
                EngagementMembershipModel.engagement_id == engagement_id,
            )
        )
        model = result.scalar_one_or_none()
        return _to_membership_entity(model) if model else None

    async def add_member(
        self, *, engagement_id: str, user_id: str, role: EngagementRole
    ) -> EngagementMembership:
        model = EngagementMembershipModel(
            engagement_id=engagement_id, user_id=user_id, role=role.value
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_membership_entity(model)

    async def list_for_current_user(self) -> list[EngagementMembership]:
        result = await self._session.execute(
            select(EngagementMembershipModel).where(
                text("user_id = current_setting('app.current_user_id', true)::uuid")
            )
        )
        return [_to_membership_entity(m) for m in result.scalars().all()]

    async def list_roster_for_engagement(self, engagement_id: str) -> list[EngagementRosterEntry]:
        result = await self._session.execute(
            select(EngagementMembershipModel, UserModel)
            .join(UserModel, UserModel.id == EngagementMembershipModel.user_id)
            .where(EngagementMembershipModel.engagement_id == engagement_id)
            .order_by(EngagementMembershipModel.granted_at)
        )
        return [
            EngagementRosterEntry(
                user_id=str(user.id),
                display_name=user.display_name,
                email=user.email,
                role=EngagementRole(membership.role),
                granted_at=membership.granted_at,
            )
            for membership, user in result.all()
        ]

    async def list_for_current_user_with_engagement_names(self) -> list[EngagementSummary]:
        result = await self._session.execute(
            select(EngagementMembershipModel, EngagementModel)
            .join(EngagementModel, EngagementModel.id == EngagementMembershipModel.engagement_id)
            .where(text("user_id = current_setting('app.current_user_id', true)::uuid"))
        )
        return [
            EngagementSummary(
                engagement_id=str(engagement.id),
                name=engagement.name,
                role=EngagementRole(membership.role),
            )
            for membership, engagement in result.all()
        ]
