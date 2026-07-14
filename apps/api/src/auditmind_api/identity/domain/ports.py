"""Repository ports (Phase 3 §1) — interfaces the application layer depends on.

The infrastructure layer provides the only implementations of these protocols; the application
layer imports these ports, never a concrete SQLAlchemy repository, so the persistence technology
can change without touching application logic.
"""

from __future__ import annotations

from typing import Protocol

from auditmind_api.identity.domain.entities import (
    EngagementMembership,
    EngagementRole,
    EngagementRosterEntry,
    EngagementSummary,
    User,
)


class UserRepository(Protocol):
    async def get_by_entra_object_id(self, entra_object_id: str) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def create(self, *, entra_object_id: str, display_name: str, email: str) -> User: ...


class CredentialRepository(Protocol):
    """Local email/password credentials — the self-service-signup counterpart to Entra JIT
    provisioning. Deliberately never exposes the hash itself past this port's return boundary in
    a form the application layer stores or logs; ``verify`` (on :class:`PasswordHasher`) is the
    only thing that ever compares against it."""

    async def create(self, *, user_id: str, password_hash: str) -> None: ...

    async def get_password_hash(self, user_id: str) -> str | None: ...


class PasswordHasher(Protocol):
    """A port so the application layer can be unit-tested against a fake hasher (Phase 3 §1) —
    the concrete algorithm (bcrypt) is an infrastructure decision, not a domain one."""

    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str) -> bool: ...


class RlsContextBinder(Protocol):
    """Binds the Row-Level Security session context for a *just-created* user, before that user
    has ever authenticated a request — the same ``set_rls_user_context`` call
    ``get_current_db_user`` makes on every request, injected as a port here so the application
    layer can call it without importing infrastructure/SQLAlchemy directly (Phase 3 §1)."""

    async def bind(self, *, user_id: str) -> None: ...


class EngagementMembershipRepository(Protocol):
    async def get_membership(
        self, *, user_id: str, engagement_id: str
    ) -> EngagementMembership | None: ...

    async def add_member(
        self, *, engagement_id: str, user_id: str, role: EngagementRole
    ) -> EngagementMembership:
        """Inserts a membership row. The only caller today is self-service signup granting a new
        user membership on themselves — safe under the ``engagement_members_self_insert`` RLS
        policy (migration ``d4a19e6b7f31``) only because the caller has already bound the RLS
        context to that same user id via :class:`RlsContextBinder` first; the database rejects
        any insert whose ``user_id`` doesn't match, regardless of what this method is passed."""
        ...

    async def list_for_current_user(self) -> list[EngagementMembership]:
        """Every membership row for the caller themselves — never a different user's, and
        deliberately takes no ``user_id`` parameter, so it is structurally impossible for a caller
        to (accidentally or otherwise) query on behalf of someone else. Scoped by the Row-Level
        Security context already bound on the session (Phase 4 §12) rather than a Python-supplied
        value, backstopped by (but, since the roster-visibility policy widened what RLS alone
        permits, no longer solely reliant on) RLS to narrow the result."""
        ...

    async def list_roster_for_engagement(self, engagement_id: str) -> list[EngagementRosterEntry]:
        """Every member of one engagement, joined with their user profile. Relies on the
        ``engagement_members_engagement_roster`` RLS policy (not an application-level filter) to
        make this safe: a caller who isn't themselves a member of ``engagement_id`` gets an empty
        result no matter what id is passed, because the database never returns rows for an
        engagement the RLS context isn't a member of."""
        ...

    async def list_for_current_user_with_engagement_names(self) -> list[EngagementSummary]:
        """Same scoping discipline as :meth:`list_for_current_user` (RLS-bound session context,
        no ``user_id`` parameter to get wrong) — joined with ``identity.engagements`` so the
        caller gets a real name to display instead of a bare id."""
        ...
