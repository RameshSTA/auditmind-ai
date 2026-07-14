"""Application services — orchestrate domain entities through ports. No SQL, no HTTP, no
framework import here; this is the layer unit tests exercise against fakes."""

from __future__ import annotations

import uuid

from auditmind_api.identity.domain.entities import (
    EngagementMembership,
    EngagementRole,
    EngagementRosterEntry,
    EngagementSummary,
    User,
)
from auditmind_api.identity.domain.ports import (
    CredentialRepository,
    EngagementMembershipRepository,
    PasswordHasher,
    RlsContextBinder,
    UserRepository,
)
from auditmind_api.shared.errors import AuthenticationError, AuthorizationError, ValidationError

# Self-service signup only ever grants roles a person can legitimately choose for themselves.
# Admin is deliberately excluded — it is a privileged, tenant-wide role that must be granted by an
# existing administrator, never claimed at signup.
SELF_SERVICE_ROLES = frozenset(
    {
        EngagementRole.AUDITOR,
        EngagementRole.FRAUD_ANALYST,
        EngagementRole.COMPLIANCE_MANAGER,
        EngagementRole.CAE,
    }
)

# A synthetic, unmistakably-local prefix for the ``entra_object_id`` a self-service signup gets —
# never collides with a real Entra object id (a GUID with no such prefix), and honestly marks the
# row as locally-registered rather than fabricating an Entra-looking identifier.
LOCAL_IDENTITY_PREFIX = "local:"


class IdentityService:
    def __init__(
        self,
        user_repository: UserRepository,
        membership_repository: EngagementMembershipRepository,
        credential_repository: CredentialRepository | None = None,
        password_hasher: PasswordHasher | None = None,
        rls_context_binder: RlsContextBinder | None = None,
        default_engagement_id: str | None = None,
    ) -> None:
        self._users = user_repository
        self._memberships = membership_repository
        # Optional: only the register/authenticate paths below need these, and every other call
        # site (JIT provisioning, membership checks) constructs this service without them.
        self._credentials = credential_repository
        self._hasher = password_hasher
        self._rls_context_binder = rls_context_binder
        self._default_engagement_id = default_engagement_id

    async def register(
        self, *, email: str, password: str, display_name: str, role: EngagementRole
    ) -> tuple[User, EngagementMembership]:
        """Self-service signup: creates a real ``identity.users`` row and a real bcrypt-hashed
        credential, then auto-joins the one demo engagement this environment has (Settings'
        ``default_engagement_id``) with the role the person picked at signup.

        This is the local counterpart to Entra JIT provisioning — same target tables, same RLS
        enforcement, different identity source. A real member-invite flow (an Admin/CAE adding an
        already-registered user to a *different* engagement) is a separate, unbuilt feature; this
        method only ever grants membership on the one engagement a fresh signup can join.
        """
        assert self._credentials and self._hasher and self._rls_context_binder
        assert self._default_engagement_id
        if role not in SELF_SERVICE_ROLES:
            allowed = ", ".join(sorted(r.value for r in SELF_SERVICE_ROLES))
            raise ValidationError(f"Role must be one of: {allowed}.")
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise ValidationError("A valid email address is required.")
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters.")
        if not display_name.strip():
            raise ValidationError("Display name is required.")

        existing = await self._users.get_by_email(normalized_email)
        if existing is not None:
            raise ValidationError("An account with this email already exists.")

        entra_object_id = f"{LOCAL_IDENTITY_PREFIX}{uuid.uuid4()}"
        user = await self._users.create(
            entra_object_id=entra_object_id,
            display_name=display_name.strip(),
            email=normalized_email,
        )
        await self._credentials.create(
            user_id=user.id, password_hash=self._hasher.hash(password)
        )
        # Binds RLS to the user we just created — the same call `get_current_db_user` makes for
        # every authenticated request — so the membership insert below satisfies the
        # `engagement_members_self_insert` policy (a caller may only ever insert a row for
        # themselves).
        await self._rls_context_binder.bind(user_id=user.id)
        membership = await self._memberships.add_member(
            engagement_id=self._default_engagement_id, user_id=user.id, role=role
        )
        return user, membership

    async def authenticate(self, *, email: str, password: str) -> User:
        """Verifies email/password against ``identity.credentials``. Returns the same
        generic-looking error whether the email is unknown or the password is wrong — never
        revealing which, so a login attempt can't be used to enumerate registered accounts."""
        assert self._credentials and self._hasher
        normalized_email = email.strip().lower()
        user = await self._users.get_by_email(normalized_email)
        if user is None:
            raise AuthenticationError("Invalid email or password.")
        password_hash = await self._credentials.get_password_hash(user.id)
        if password_hash is None or not self._hasher.verify(password, password_hash):
            raise AuthenticationError("Invalid email or password.")
        return user

    async def resolve_or_provision_user(
        self, *, entra_object_id: str, display_name: str, email: str
    ) -> User:
        """Just-in-time provisioning: the first successfully-authenticated request from a given
        Entra identity creates its corresponding ``identity.users`` row.

        This is standard practice for enterprise SSO integrations — Entra ID remains the sole
        source of truth for *whether someone may authenticate at all*; this only maintains the
        internal identity that engagement-scoped authorization (below) is keyed on.
        """
        existing = await self._users.get_by_entra_object_id(entra_object_id)
        if existing is not None:
            return existing
        return await self._users.create(
            entra_object_id=entra_object_id, display_name=display_name, email=email
        )

    async def require_membership(
        self,
        *,
        user_id: str,
        engagement_id: str,
        allowed_roles: frozenset[EngagementRole] | None = None,
    ) -> EngagementMembership:
        """Looked up fresh from the database on every call — never from a cached JWT claim.

        Engagement membership can change intra-day (reassignment, conflict-of-interest removal),
        so it is never safe to trust a value cached in a token for its lifetime the way a coarse
        role claim is.
        """
        membership = await self._memberships.get_membership(
            user_id=user_id, engagement_id=engagement_id
        )
        if membership is None:
            raise AuthorizationError("You are not a member of this engagement.")
        if allowed_roles is not None and membership.role not in allowed_roles:
            required = ", ".join(sorted(role.value for role in allowed_roles))
            raise AuthorizationError(
                f"This action requires one of these engagement roles: {required}."
            )
        return membership

    async def get_membership(
        self, *, user_id: str, engagement_id: str
    ) -> EngagementMembership | None:
        """A non-raising lookup — unlike ``require_membership``, a caller with no membership on
        ``engagement_id`` is a normal, expected outcome here (e.g. rendering a login response's
        role claim), not an authorization failure."""
        return await self._memberships.get_membership(user_id=user_id, engagement_id=engagement_id)

    async def list_current_user_memberships(self) -> list[EngagementMembership]:
        """Returns only the RLS-scoped caller's own memberships — see
        ``EngagementMembershipRepository.list_for_current_user``'s docstring for why there is
        deliberately no ``user_id`` argument to pass through here."""
        return await self._memberships.list_for_current_user()

    async def list_current_user_engagements(self) -> list[EngagementSummary]:
        """Same scoping as :meth:`list_current_user_memberships`, joined with each engagement's
        real name — what the portfolio dashboard and the engagement breadcrumb show instead of a
        bare id."""
        return await self._memberships.list_for_current_user_with_engagement_names()

    async def get_current_user_engagement(self, engagement_id: str) -> EngagementSummary | None:
        """One engagement's real name for the caller, or ``None`` if they aren't a member —
        the breadcrumb's data source. Filters :meth:`list_current_user_engagements` rather than a
        dedicated single-row query: this app has no ``EngagementRepository`` port of its own yet
        (only the membership-joined read models above), and a caller's membership count is small
        enough that fetching all of them and filtering in Python is not a real cost."""
        engagements = await self.list_current_user_engagements()
        return next((e for e in engagements if e.engagement_id == engagement_id), None)

    async def list_engagement_roster(self, engagement_id: str) -> list[EngagementRosterEntry]:
        """Every member of one engagement (Administration's roster view). The caller must already
        be a member of ``engagement_id`` — enforced the same way every other engagement-scoped
        endpoint is, via ``require_engagement_member`` at the interface layer before this is ever
        called — and the RLS policy behind ``list_roster_for_engagement`` backstops that even if
        an application-layer check were ever skipped."""
        return await self._memberships.list_roster_for_engagement(engagement_id)
