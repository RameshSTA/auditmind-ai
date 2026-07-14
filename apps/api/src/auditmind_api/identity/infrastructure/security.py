"""Infrastructure adapters for the ``PasswordHasher`` and ``RlsContextBinder`` ports — the two
pieces of self-service signup that are genuinely infrastructure concerns (a hashing algorithm, a
raw session-scoped SQL call), kept out of the application layer."""

from __future__ import annotations

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.shared.database import set_rls_user_context


class BcryptPasswordHasher:
    """bcrypt is deliberately not configurable — this is the one hashing algorithm this codebase
    supports, not a pluggable policy. A cost factor of 12 (bcrypt's default) matches current
    OWASP guidance without a getting a login endpoint slow enough to be its own DoS vector."""

    def hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except ValueError:
            # A malformed/legacy hash should fail closed (reject the login), never raise past
            # this boundary and turn into a 500 on what is, from the caller's side, just a wrong
            # password.
            return False


class PostgresRlsContextBinder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bind(self, *, user_id: str) -> None:
        await set_rls_user_context(self._session, user_id=user_id)
