"""Adapter for the ``GraphStore`` port: Neo4j itself (Phase 4 §3).

**Security note, the one genuinely novel risk this context introduces**: every other bounded
context's isolation guarantee comes from Postgres Row-Level Security — the database itself refuses
to return or accept a row outside the caller's engagement, proven directly in each context's own
``test_*_rls.py`` (Increments 02-08). Neo4j has no equivalent mechanism (Phase 4 §12: "every node
carries an ``engagement_id`` property because Neo4j has no native row-level security
equivalent"). That means isolation here is an application-code guarantee, not a database one —
every single Cypher query below filters on ``engagement_id`` as part of the node pattern itself
(``MATCH (v:Vendor {id: $vendor_id, engagement_id: $engagement_id})``), never as an afterthought
post-filter, so a caller who guesses another engagement's vendor id gets a genuine "not found"
(zero rows), the same observable behavior Postgres RLS produces — proven directly by
``tests/integration/test_knowledge_graph_isolation.py`` rather than assumed to be equivalent.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from neo4j import AsyncDriver

from auditmind_api.kg.domain.entities import VendorEntity, VendorNetwork, VendorTransactionSummary


class Neo4jGraphStore:
    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def ensure_constraints(self) -> None:
        """Idempotent (``IF NOT EXISTS``) — called once at process startup (``main.py``'s
        lifespan), the closest equivalent Neo4j has to Alembic's ``upgrade head`` for the one
        thing this context needs enforced: a vendor/transaction id is only unique *within* an
        engagement, not globally, so the composite constraint includes ``engagement_id``."""
        async with self._driver.session() as session:
            await session.run(
                "CREATE CONSTRAINT vendor_id_per_engagement IF NOT EXISTS "
                "FOR (v:Vendor) REQUIRE (v.id, v.engagement_id) IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT transaction_id_per_engagement IF NOT EXISTS "
                "FOR (t:Transaction) REQUIRE (t.id, t.engagement_id) IS UNIQUE"
            )

    async def merge_vendor_and_transaction(
        self,
        *,
        engagement_id: str,
        vendor_id: str,
        vendor_name: str,
        normalized_name: str,
        transaction_id: str,
        amount: Decimal,
        currency: str,
        transaction_date: date,
    ) -> None:
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (v:Vendor {id: $vendor_id, engagement_id: $engagement_id})
                ON CREATE SET v.name = $vendor_name, v.normalized_name = $normalized_name
                MERGE (t:Transaction {id: $transaction_id, engagement_id: $engagement_id})
                ON CREATE SET t.amount = $amount, t.currency = $currency, t.date = $transaction_date
                MERGE (t)-[:PAID]->(v)
                """,
                vendor_id=vendor_id,
                engagement_id=engagement_id,
                vendor_name=vendor_name,
                normalized_name=normalized_name,
                transaction_id=transaction_id,
                # Stored as a string, not a native float — Neo4j has no arbitrary-precision
                # decimal type, and a monetary amount silently losing precision through a float
                # round-trip is exactly the kind of "cosmetic-looking but not cosmetic" bug this
                # codebase's ingestion cleaning stage already refuses to accept for the same
                # reason (see ingestion/infrastructure's own currency-formatting discipline).
                # Postgres's `risk.transactions.amount` (Numeric(18,2)) remains the system of
                # record for the exact value; this graph is an investigative read model, not the
                # ledger.
                amount=str(amount),
                currency=currency,
                transaction_date=transaction_date,
            )

    async def list_vendors(self, *, engagement_id: str) -> list[VendorEntity]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (v:Vendor {engagement_id: $engagement_id})<-[:PAID]-(t:Transaction)
                RETURN v.id AS id, v.name AS name, v.normalized_name AS normalized_name,
                       t.amount AS amount, t.currency AS currency
                """,
                engagement_id=engagement_id,
            )
            records = [record async for record in result]

        by_vendor: dict[str, dict[str, Any]] = {}
        for record in records:
            vendor_id = record["id"]
            entry = by_vendor.setdefault(
                vendor_id,
                {
                    "name": record["name"],
                    "normalized_name": record["normalized_name"],
                    "transaction_count": 0,
                    "total_amount_by_currency": defaultdict(Decimal),
                },
            )
            entry["transaction_count"] += 1
            entry["total_amount_by_currency"][record["currency"]] += Decimal(record["amount"])

        return [
            VendorEntity(
                id=vendor_id,
                engagement_id=engagement_id,
                name=entry["name"],
                normalized_name=entry["normalized_name"],
                transaction_count=entry["transaction_count"],
                total_amount_by_currency=dict(entry["total_amount_by_currency"]),
            )
            for vendor_id, entry in by_vendor.items()
        ]

    async def get_vendor_network(
        self, *, engagement_id: str, vendor_id: str
    ) -> VendorNetwork | None:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (v:Vendor {id: $vendor_id, engagement_id: $engagement_id})
                OPTIONAL MATCH (v)<-[:PAID]-(t:Transaction)
                RETURN v.id AS id, v.name AS name, v.normalized_name AS normalized_name,
                       t.id AS transaction_id, t.amount AS amount, t.currency AS currency,
                       t.date AS transaction_date
                """,
                vendor_id=vendor_id,
                engagement_id=engagement_id,
            )
            records = [record async for record in result]

        if not records:
            return None  # v itself never matched — either it doesn't exist, or belongs to
            # another engagement, both of which must be indistinguishable to the caller.

        transactions = [
            VendorTransactionSummary(
                transaction_id=record["transaction_id"],
                amount=Decimal(record["amount"]),
                currency=record["currency"],
                transaction_date=record["transaction_date"].to_native(),
            )
            for record in records
            if record["transaction_id"] is not None  # OPTIONAL MATCH found no transactions
        ]
        total_by_currency: dict[str, Decimal] = defaultdict(Decimal)
        for txn in transactions:
            total_by_currency[txn.currency] += txn.amount

        vendor = VendorEntity(
            id=records[0]["id"],
            engagement_id=engagement_id,
            name=records[0]["name"],
            normalized_name=records[0]["normalized_name"],
            transaction_count=len(transactions),
            total_amount_by_currency=dict(total_by_currency),
        )
        return VendorNetwork(vendor=vendor, transactions=transactions)
