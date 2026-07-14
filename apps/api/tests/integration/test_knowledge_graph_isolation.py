"""Integration tests proving cross-engagement isolation in Neo4j — the guarantee every other
bounded context gets for free from Postgres Row-Level Security, but that this context has to earn
through application code, since Neo4j has no equivalent mechanism (see
``kg/infrastructure/neo4j_graph_store.py``'s module docstring). Real Neo4j, real driver, no fakes
— the same "don't assume, prove it against the real thing" discipline every Postgres RLS test file
already applies, aimed at a different database engine here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from neo4j import AsyncDriver, AsyncGraphDatabase

from auditmind_api.kg.infrastructure.neo4j_graph_store import Neo4jGraphStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("AUDITMIND_NEO4J_URI"),
    reason="Requires a real Neo4j instance — set AUDITMIND_NEO4J_URI to run.",
)


@pytest_asyncio.fixture
async def driver() -> AsyncIterator[AsyncDriver]:
    uri = os.environ["AUDITMIND_NEO4J_URI"]
    user = os.environ.get("AUDITMIND_NEO4J_USER", "neo4j")
    password = os.environ.get("AUDITMIND_NEO4J_PASSWORD", "")
    d = AsyncGraphDatabase.driver(uri, auth=(user, password))
    yield d
    await d.close()


@pytest_asyncio.fixture
async def two_engagements_with_vendors(
    driver: AsyncDriver,
) -> AsyncIterator[dict[str, str]]:
    store = Neo4jGraphStore(driver)
    await store.ensure_constraints()

    ids = {
        "engagement_1": str(uuid.uuid4()),
        "engagement_2": str(uuid.uuid4()),
        "vendor_1": str(uuid.uuid4()),
        "vendor_2": str(uuid.uuid4()),
        "transaction_1": str(uuid.uuid4()),
        "transaction_2": str(uuid.uuid4()),
    }
    await store.merge_vendor_and_transaction(
        engagement_id=ids["engagement_1"],
        vendor_id=ids["vendor_1"],
        vendor_name="Acme Corp",
        normalized_name="acme corp",
        transaction_id=ids["transaction_1"],
        amount=Decimal("100.00"),
        currency="USD",
        transaction_date=date(2026, 1, 1),
    )
    await store.merge_vendor_and_transaction(
        engagement_id=ids["engagement_2"],
        vendor_id=ids["vendor_2"],
        vendor_name="Globex Inc",
        normalized_name="globex inc",
        transaction_id=ids["transaction_2"],
        amount=Decimal("200.00"),
        currency="USD",
        transaction_date=date(2026, 1, 2),
    )

    yield ids

    async with driver.session() as session:
        await session.run(
            "MATCH (n) WHERE n.engagement_id IN [$e1, $e2] DETACH DELETE n",
            e1=ids["engagement_1"],
            e2=ids["engagement_2"],
        )


async def test_list_vendors_only_returns_the_requested_engagements_vendors(
    driver: AsyncDriver, two_engagements_with_vendors: dict[str, str]
) -> None:
    store = Neo4jGraphStore(driver)

    vendors = await store.list_vendors(engagement_id=two_engagements_with_vendors["engagement_1"])

    assert len(vendors) == 1
    assert vendors[0].id == two_engagements_with_vendors["vendor_1"]


async def test_get_vendor_network_returns_none_for_a_vendor_id_from_another_engagement(
    driver: AsyncDriver, two_engagements_with_vendors: dict[str, str]
) -> None:
    """The critical isolation proof: `vendor_2` genuinely exists in Neo4j, but a caller scoped to
    `engagement_1` asking for it by its exact id gets nothing back — the same observable behavior
    Postgres RLS produces for a cross-engagement id guess, proven here because Neo4j has no
    database-level mechanism to produce it automatically."""
    store = Neo4jGraphStore(driver)

    network = await store.get_vendor_network(
        engagement_id=two_engagements_with_vendors["engagement_1"],
        vendor_id=two_engagements_with_vendors["vendor_2"],
    )

    assert network is None


async def test_get_vendor_network_returns_the_network_for_the_owning_engagement(
    driver: AsyncDriver, two_engagements_with_vendors: dict[str, str]
) -> None:
    store = Neo4jGraphStore(driver)

    network = await store.get_vendor_network(
        engagement_id=two_engagements_with_vendors["engagement_1"],
        vendor_id=two_engagements_with_vendors["vendor_1"],
    )

    assert network is not None
    assert network.vendor.id == two_engagements_with_vendors["vendor_1"]
    assert len(network.transactions) == 1
    assert network.transactions[0].transaction_id == two_engagements_with_vendors["transaction_1"]


async def test_merge_vendor_and_transaction_is_idempotent(
    driver: AsyncDriver, two_engagements_with_vendors: dict[str, str]
) -> None:
    """Re-running the same merge (the "self-healing" property `KnowledgeGraphService.
    resolve_vendors` relies on) does not create duplicate nodes or duplicate PAID edges."""
    store = Neo4jGraphStore(driver)
    ids = two_engagements_with_vendors

    await store.merge_vendor_and_transaction(
        engagement_id=ids["engagement_1"],
        vendor_id=ids["vendor_1"],
        vendor_name="Acme Corp",
        normalized_name="acme corp",
        transaction_id=ids["transaction_1"],
        amount=Decimal("100.00"),
        currency="USD",
        transaction_date=date(2026, 1, 1),
    )

    network = await store.get_vendor_network(
        engagement_id=ids["engagement_1"], vendor_id=ids["vendor_1"]
    )
    assert network is not None
    assert len(network.transactions) == 1  # not 2
