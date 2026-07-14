"""Entra ID (Azure AD) JWT validation.

Every access token is validated against Entra's published JWKS — signature, issuer, audience, and
expiry (with clock-skew leeway) are all checked before any claim is trusted.

Only coarse role claims are ever trusted from the token itself: fine-grained engagement/tenant
scope is deliberately *not* read from the JWT anywhere in this codebase, because role assignment
changes rarely enough to be safe to cache for a token's short lifetime, while engagement membership
can change intra-day and must always be re-checked against the database
(``identity.engagement_members``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from fastapi import Depends, Request
from jose import jwk, jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError

from auditmind_api.shared.errors import AuthenticationError, AuthorizationError
from auditmind_api.shared.settings import Settings, get_settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AuthenticatedUser:
    """The identity extracted from a validated access token."""

    subject: str
    roles: frozenset[str]
    tenant_id: str | None


class JWKSClient:
    """Fetches and time-caches Entra ID's signing keys.

    A class (not a module-level dict) specifically so tests can substitute a fake client that
    returns a locally-generated test key instead of making a real network call to Entra.
    """

    def __init__(self, jwks_uri: str, cache_ttl_seconds: int = 3600) -> None:
        self._jwks_uri = jwks_uri
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_keys: dict[str, Any] | None = None
        self._cached_at: float = 0.0

    async def get_signing_key(self, kid: str) -> dict[str, Any]:
        """Returns the JWK matching ``kid``, refreshing the cache if it's stale or missing."""
        cache_is_stale = (time.monotonic() - self._cached_at) > self._cache_ttl_seconds
        if self._cached_keys is None or cache_is_stale:
            await self._refresh()

        key = self._find_key(kid)
        if key is not None:
            return key

        # Key not found even after a fresh-enough cache — Entra may have just rotated keys.
        # Refresh once more before giving up, rather than rejecting a token that is actually valid.
        await self._refresh()
        key = self._find_key(kid)
        if key is not None:
            return key

        raise AuthenticationError("Token was signed with an unrecognized key.")

    def _find_key(self, kid: str) -> dict[str, Any] | None:
        if self._cached_keys is None:
            return None
        for key in self._cached_keys.get("keys", []):
            if key.get("kid") == kid:
                key_dict: dict[str, Any] = key
                return key_dict
        return None

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._jwks_uri)
            response.raise_for_status()
            self._cached_keys = response.json()
            self._cached_at = time.monotonic()


def decode_and_validate_token(
    token: str,
    signing_key: dict[str, Any],
    *,
    audience: str,
    issuer: str,
    leeway_seconds: int,
) -> dict[str, Any]:
    """Validates signature, issuer, audience, and expiry. Raises AuthenticationError on failure.

    This function has no I/O — it is pure validation logic given a token and an already-fetched
    key, which is what makes it directly unit-testable without a network call or a running app.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwk.construct(signing_key),
            algorithms=[signing_key.get("alg", "RS256")],
            audience=audience,
            issuer=issuer,
            options={"leeway": leeway_seconds},
        )
    except ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired.") from exc
    except JWTClaimsError as exc:
        raise AuthenticationError(f"Access token claims are invalid: {exc}") from exc
    except JWTError as exc:
        raise AuthenticationError("Access token signature could not be verified.") from exc

    return claims


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise AuthenticationError("Missing or malformed Authorization header.")
    return auth_header.split(" ", 1)[1].strip()


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """FastAPI dependency: validates the bearer token and returns the caller's identity.

    The JWKS client is read from ``request.app.state`` (set once at startup in ``main.py``)
    rather than constructed per-request, so key caching (above) actually takes effect.
    """
    token = _extract_bearer_token(request)

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise AuthenticationError("Access token header could not be parsed.") from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise AuthenticationError("Access token header is missing a key id.")

    jwks_client: JWKSClient = request.app.state.jwks_client
    signing_key = await jwks_client.get_signing_key(kid)

    claims = decode_and_validate_token(
        token,
        signing_key,
        audience=settings.entra_client_id,
        issuer=settings.entra_issuer,
        leeway_seconds=settings.jwt_leeway_seconds,
    )

    roles = frozenset(claims.get("roles", []))
    if not roles:
        logger.warning("token_has_no_roles", subject=claims.get("sub"))

    return AuthenticatedUser(
        subject=claims["sub"],
        roles=roles,
        tenant_id=claims.get("tid"),
    )


def require_role(*allowed_roles: str) -> Any:
    """FastAPI dependency factory enforcing coarse RBAC.

    Usage: ``Depends(require_role("Admin"))`` or ``Depends(require_role("Auditor", "CAE"))``.
    """

    async def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not user.roles.intersection(allowed_roles):
            required = ", ".join(sorted(allowed_roles))
            raise AuthorizationError(f"This action requires one of these roles: {required}.")
        return user

    return _check
