"""The single SQLAlchemy declarative base every bounded context's ORM models register onto.

Living in ``shared/`` rather than inside any one context's ``infrastructure/`` package is
deliberate: Alembic's ``env.py`` needs one ``MetaData`` object that has every table registered on
it for autogenerate to see the whole schema, but no bounded context should import another
context's infrastructure module to get it. This is the same category of shared concern as
``shared/database.py``'s engine — infrastructure that is genuinely common, not a backdoor between
contexts.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
