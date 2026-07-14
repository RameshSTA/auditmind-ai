"""The single SQLAlchemy declarative base for this service's ORM models.

This service is a separate deployable (ADR-001) with its own Alembic migration history, so it has
its own ``Base`` rather than importing ``apps/api``'s — the two services never share a Python
package. They *do* share one physical Postgres database, and the *migration DDL* declares real
cross-schema ``FOREIGN KEY`` constraints to ``identity.*`` (enforced by Postgres) — but the ORM
*models* in ``infrastructure/models.py`` deliberately do not repeat those as SQLAlchemy-level
``ForeignKey`` objects, because this ``Base``'s metadata has no
``identity.users``/``identity.engagements`` ``Table`` registered on it to resolve them against (see
that module's own docstring for the exact error this avoids).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
