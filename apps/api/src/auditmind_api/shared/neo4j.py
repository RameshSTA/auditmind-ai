"""Async Neo4j driver singleton.

Neo4j has no equivalent to Postgres's least-privilege role + Row-Level Security story
(``shared/database.py``) — there is no per-request session variable a policy can check. Every
node this codebase writes carries an ``engagement_id`` property, and every Cypher query the ``kg``
context issues filters on it explicitly. This module only manages the driver connection; the
isolation guarantee itself lives in ``kg/infrastructure/neo4j_graph_store.py``, verified by
``tests/integration/test_knowledge_graph_isolation.py`` rather than assumed to inherit Postgres's.
"""

from __future__ import annotations

from neo4j import AsyncDriver, AsyncGraphDatabase

from auditmind_api.shared.settings import Settings, get_settings

_driver: AsyncDriver | None = None


def get_neo4j_driver(settings: Settings | None = None) -> AsyncDriver:
    """Process-wide async driver singleton — the driver itself already pools connections
    internally, so there is no per-request equivalent to ``shared/database.py``'s session
    factory; callers open a session per unit of work from this one shared driver."""
    global _driver
    if _driver is None:
        resolved_settings = settings or get_settings()
        _driver = AsyncGraphDatabase.driver(
            resolved_settings.neo4j_uri,
            auth=(resolved_settings.neo4j_user, resolved_settings.neo4j_password),
        )
    return _driver


async def close_neo4j_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
