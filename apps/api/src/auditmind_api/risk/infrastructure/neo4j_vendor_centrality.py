"""Adapter for the ``VendorCentralitySource`` port: reads ``kg``'s Neo4j graph directly via the
shared driver (``shared/neo4j.py``) without importing ``kg``'s ``Neo4jGraphStore`` class — the
Neo4j equivalent of every raw-SQL cross-context read in this codebase (``chunk_lookup.py``,
``chunk_text_source.py``, ...), applied to a different database for the first time.

Filters on ``engagement_id`` as part of the Cypher pattern itself, the same discipline
``kg/infrastructure/neo4j_graph_store.py``'s own module docstring establishes and its isolation
tests prove — Neo4j has no Row-Level Security, so this adapter is exactly as responsible for
correct isolation as `kg`'s own adapter is, not exempt from that discipline just because it's a
different bounded context's code touching the same database.
"""

from __future__ import annotations

from neo4j import AsyncDriver


class Neo4jVendorCentralitySource:
    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def get_vendor_transaction_counts(self, *, engagement_id: str) -> dict[str, int]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (v:Vendor {engagement_id: $engagement_id})<-[:PAID]-(t:Transaction)
                RETURN v.normalized_name AS normalized_name, count(t) AS transaction_count
                """,
                engagement_id=engagement_id,
            )
            records = [record async for record in result]
        return {record["normalized_name"]: record["transaction_count"] for record in records}
