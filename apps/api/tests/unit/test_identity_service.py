"""Unit tests for IdentityService (Phase 3 §1 application layer).

Exercised entirely against in-memory fakes implementing the repository ports — no database
involved. This is exactly what the hexagonal layering (Phase 3 §1) buys: application logic is
testable without a real Postgres connection at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from auditmind_api.identity.application.services import IdentityService
from auditmind_api.identity.domain.entities import (
    EngagementMembership,
    EngagementRole,
    EngagementRosterEntry,
    EngagementSummary,
    User,
)
from auditmind_api.shared.errors import AuthenticationError, AuthorizationError, ValidationError


class FakeUserRepository:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    async def get_by_entra_object_id(self, entra_object_id: str) -> User | None:
        return self._users.get(entra_object_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self._users.values() if u.email == email), None)

    async def create(self, *, entra_object_id: str, display_name: str, email: str) -> User:
        user = User(
            id=f"user-{len(self._users) + 1}",
            entra_object_id=entra_object_id,
            display_name=display_name,
            email=email,
            created_at=datetime.now(UTC),
        )
        self._users[entra_object_id] = user
        return user


class FakeMembershipRepository:
    def __init__(
        self,
        memberships: list[EngagementMembership] | None = None,
        roster_by_engagement: dict[str, list[EngagementRosterEntry]] | None = None,
        engagement_names: dict[str, str] | None = None,
    ) -> None:
        self._memberships = memberships or []
        self._roster_by_engagement = roster_by_engagement or {}
        self._engagement_names = engagement_names or {}

    async def get_membership(
        self, *, user_id: str, engagement_id: str
    ) -> EngagementMembership | None:
        for m in self._memberships:
            if m.user_id == user_id and m.engagement_id == engagement_id:
                return m
        return None

    async def add_member(
        self, *, engagement_id: str, user_id: str, role: EngagementRole
    ) -> EngagementMembership:
        membership = _membership(engagement_id=engagement_id, user_id=user_id, role=role)
        self._memberships.append(membership)
        return membership

    async def list_for_current_user(self) -> list[EngagementMembership]:
        return list(self._memberships)

    async def list_roster_for_engagement(self, engagement_id: str) -> list[EngagementRosterEntry]:
        return list(self._roster_by_engagement.get(engagement_id, []))

    async def list_for_current_user_with_engagement_names(self) -> list[EngagementSummary]:
        return [
            EngagementSummary(
                engagement_id=m.engagement_id,
                name=self._engagement_names.get(m.engagement_id, "Unnamed engagement"),
                role=m.role,
            )
            for m in self._memberships
        ]


class FakeCredentialRepository:
    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}

    async def create(self, *, user_id: str, password_hash: str) -> None:
        self._hashes[user_id] = password_hash

    async def get_password_hash(self, user_id: str) -> str | None:
        return self._hashes.get(user_id)


class FakePasswordHasher:
    """Not a real hash — a fake standing in for the ``PasswordHasher`` port so this layer's tests
    never depend on bcrypt's actual algorithm, only on the port's contract."""

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeRlsContextBinder:
    def __init__(self) -> None:
        self.bound_user_ids: list[str] = []

    async def bind(self, *, user_id: str) -> None:
        self.bound_user_ids.append(user_id)


def _membership(
    *,
    engagement_id: str = "eng-1",
    user_id: str = "user-1",
    role: EngagementRole = EngagementRole.AUDITOR,
) -> EngagementMembership:
    return EngagementMembership(
        engagement_id=engagement_id, user_id=user_id, role=role, granted_at=datetime.now(UTC)
    )


@pytest.fixture
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


async def test_resolve_or_provision_user_creates_on_first_call(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(user_repo, FakeMembershipRepository())

    user = await service.resolve_or_provision_user(
        entra_object_id="entra-1", display_name="Raj Patel", email="raj@example.com"
    )

    assert user.entra_object_id == "entra-1"
    assert user.display_name == "Raj Patel"


async def test_resolve_or_provision_user_returns_existing_on_second_call(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(user_repo, FakeMembershipRepository())

    first = await service.resolve_or_provision_user(
        entra_object_id="entra-1", display_name="Raj Patel", email="raj@example.com"
    )
    second = await service.resolve_or_provision_user(
        entra_object_id="entra-1",
        display_name="Ignored — already provisioned",
        email="raj@example.com",
    )

    assert first.id == second.id
    assert second.display_name == "Raj Patel"  # JIT provisioning only creates, never overwrites


async def test_require_membership_succeeds_for_a_real_member(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(user_repo, FakeMembershipRepository([_membership()]))

    result = await service.require_membership(user_id="user-1", engagement_id="eng-1")

    assert result.role == EngagementRole.AUDITOR


async def test_require_membership_rejects_a_non_member(user_repo: FakeUserRepository) -> None:
    service = IdentityService(user_repo, FakeMembershipRepository([]))

    with pytest.raises(AuthorizationError, match="not a member"):
        await service.require_membership(user_id="user-1", engagement_id="eng-1")


async def test_require_membership_enforces_role_restriction(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(
        user_repo, FakeMembershipRepository([_membership(role=EngagementRole.AUDITOR)])
    )

    with pytest.raises(AuthorizationError, match="requires one of these engagement roles"):
        await service.require_membership(
            user_id="user-1", engagement_id="eng-1", allowed_roles=frozenset({EngagementRole.CAE})
        )


async def test_require_membership_allows_when_role_matches_restriction(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(
        user_repo, FakeMembershipRepository([_membership(role=EngagementRole.CAE)])
    )

    result = await service.require_membership(
        user_id="user-1",
        engagement_id="eng-1",
        allowed_roles=frozenset({EngagementRole.CAE, EngagementRole.ADMIN}),
    )

    assert result.role == EngagementRole.CAE


async def test_list_current_user_memberships_delegates_to_repository(
    user_repo: FakeUserRepository,
) -> None:
    memberships = [
        _membership(engagement_id="eng-1", role=EngagementRole.AUDITOR),
        _membership(engagement_id="eng-2", role=EngagementRole.FRAUD_ANALYST),
    ]
    service = IdentityService(user_repo, FakeMembershipRepository(memberships))

    result = await service.list_current_user_memberships()

    assert len(result) == 2
    assert {m.engagement_id for m in result} == {"eng-1", "eng-2"}


async def test_list_current_user_engagements_includes_the_real_name(
    user_repo: FakeUserRepository,
) -> None:
    memberships = [_membership(engagement_id="eng-1", role=EngagementRole.AUDITOR)]
    membership_repo = FakeMembershipRepository(
        memberships, engagement_names={"eng-1": "Acme Corp FY26 Audit"}
    )
    service = IdentityService(user_repo, membership_repo)

    result = await service.list_current_user_engagements()

    assert len(result) == 1
    assert result[0].name == "Acme Corp FY26 Audit"
    assert result[0].role == EngagementRole.AUDITOR


async def test_get_current_user_engagement_returns_none_for_a_non_member(
    user_repo: FakeUserRepository,
) -> None:
    membership_repo = FakeMembershipRepository(
        [_membership(engagement_id="eng-1")], engagement_names={"eng-1": "Acme Corp FY26 Audit"}
    )
    service = IdentityService(user_repo, membership_repo)

    result = await service.get_current_user_engagement("eng-2")

    assert result is None


async def test_list_engagement_roster_delegates_to_repository(
    user_repo: FakeUserRepository,
) -> None:
    roster = [
        EngagementRosterEntry(
            user_id="user-1",
            display_name="Raj Patel",
            email="raj@example.com",
            role=EngagementRole.AUDITOR,
            granted_at=datetime.now(UTC),
        ),
        EngagementRosterEntry(
            user_id="user-2",
            display_name="Amara Okafor",
            email="amara@example.com",
            role=EngagementRole.FRAUD_ANALYST,
            granted_at=datetime.now(UTC),
        ),
    ]
    service = IdentityService(
        user_repo, FakeMembershipRepository(roster_by_engagement={"eng-1": roster})
    )

    result = await service.list_engagement_roster("eng-1")

    assert result == roster


async def test_list_engagement_roster_is_empty_for_an_engagement_with_no_roster_entries(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(user_repo, FakeMembershipRepository())

    result = await service.list_engagement_roster("eng-unknown")

    assert result == []


def _registration_service(
    user_repo: FakeUserRepository | None = None,
) -> tuple[IdentityService, FakeCredentialRepository, FakeRlsContextBinder]:
    credentials = FakeCredentialRepository()
    rls_binder = FakeRlsContextBinder()
    service = IdentityService(
        user_repo or FakeUserRepository(),
        FakeMembershipRepository(),
        credential_repository=credentials,
        password_hasher=FakePasswordHasher(),
        rls_context_binder=rls_binder,
        default_engagement_id="demo-engagement",
    )
    return service, credentials, rls_binder


async def test_register_creates_a_real_user_credential_and_membership() -> None:
    service, credentials, rls_binder = _registration_service()

    user, membership = await service.register(
        email="Jane.Doe@Example.com",
        password="correct-horse-battery",
        display_name="Jane Doe",
        role=EngagementRole.AUDITOR,
    )

    assert user.email == "jane.doe@example.com"  # normalized
    assert user.entra_object_id.startswith("local:")
    assert membership.engagement_id == "demo-engagement"
    assert membership.role == EngagementRole.AUDITOR
    assert await credentials.get_password_hash(user.id) == "hashed:correct-horse-battery"
    assert rls_binder.bound_user_ids == [user.id]


async def test_register_rejects_the_admin_role() -> None:
    service, _, _ = _registration_service()

    with pytest.raises(ValidationError, match="Role must be one of"):
        await service.register(
            email="admin@example.com",
            password="correct-horse-battery",
            display_name="Would-be Admin",
            role=EngagementRole.ADMIN,
        )


async def test_register_rejects_a_duplicate_email() -> None:
    service, _, _ = _registration_service()
    await service.register(
        email="dup@example.com",
        password="correct-horse-battery",
        display_name="First",
        role=EngagementRole.AUDITOR,
    )

    with pytest.raises(ValidationError, match="already exists"):
        await service.register(
            email="dup@example.com",
            password="another-password",
            display_name="Second",
            role=EngagementRole.CAE,
        )


async def test_register_rejects_a_short_password() -> None:
    service, _, _ = _registration_service()

    with pytest.raises(ValidationError, match="at least 8 characters"):
        await service.register(
            email="short@example.com",
            password="short",
            display_name="Short Password",
            role=EngagementRole.AUDITOR,
        )


async def test_authenticate_succeeds_with_the_correct_password() -> None:
    service, _, _ = _registration_service()
    registered, _ = await service.register(
        email="jane@example.com",
        password="correct-horse-battery",
        display_name="Jane Doe",
        role=EngagementRole.AUDITOR,
    )

    authenticated = await service.authenticate(
        email="jane@example.com", password="correct-horse-battery"
    )

    assert authenticated.id == registered.id


async def test_authenticate_rejects_the_wrong_password() -> None:
    service, _, _ = _registration_service()
    await service.register(
        email="jane@example.com",
        password="correct-horse-battery",
        display_name="Jane Doe",
        role=EngagementRole.AUDITOR,
    )

    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        await service.authenticate(email="jane@example.com", password="wrong-password")


async def test_authenticate_rejects_an_unknown_email() -> None:
    service, _, _ = _registration_service()

    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        await service.authenticate(email="nobody@example.com", password="whatever12345")


async def test_get_membership_returns_none_for_a_non_member(
    user_repo: FakeUserRepository,
) -> None:
    service = IdentityService(user_repo, FakeMembershipRepository())

    result = await service.get_membership(user_id="user-1", engagement_id="eng-1")

    assert result is None
