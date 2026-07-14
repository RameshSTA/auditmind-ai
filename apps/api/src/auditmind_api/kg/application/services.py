"""Application service for the Knowledge Graph context. No SQL, no Cypher, no HTTP — only
coordination of ports, the same pattern every prior context's service established."""

from __future__ import annotations

import uuid
from collections import defaultdict

from auditmind_api.kg.domain.entities import VendorEntity, VendorNetwork, normalize_vendor_name
from auditmind_api.kg.domain.ports import (
    EntityCandidateRepository,
    GraphStore,
    TransactionForResolution,
    TransactionSource,
)
from auditmind_api.shared.errors import NotFoundError


class KnowledgeGraphService:
    def __init__(
        self,
        transaction_source: TransactionSource,
        candidate_repository: EntityCandidateRepository,
        graph_store: GraphStore,
    ) -> None:
        self._transaction_source = transaction_source
        self._candidate_repository = candidate_repository
        self._graph_store = graph_store

    async def resolve_vendors(self, *, engagement_id: str) -> int:
        """Reads every transaction with a vendor name, groups by normalized name so every raw
        spelling variant of the same vendor resolves to one stable graph identity, and upserts the
        result into both the Postgres bridge tables and Neo4j.

        Idempotent and self-healing by design, not just by accident: safe to call repeatedly on
        the same engagement (e.g. after importing more transactions) because (1) a previously-
        resolved vendor name is looked up and reused, never re-minted, and (2) every Neo4j write is
        a ``MERGE``, so even a transaction whose Postgres candidate row already exists still gets
        its graph edge re-asserted — a partial failure in an earlier run (Postgres written, Neo4j
        write dropped, or vice versa) is corrected by simply running this again, not by a separate
        repair path. Returns the number of newly recorded candidates this run (informational only,
        see ``EntityCandidateRepository.record_candidate_and_resolution``'s docstring).
        """
        await self._graph_store.ensure_constraints()

        transactions = await self._transaction_source.list_transactions_with_vendor(
            engagement_id=engagement_id
        )

        groups: dict[str, list[TransactionForResolution]] = defaultdict(list)
        for txn in transactions:
            groups[normalize_vendor_name(txn.vendor_name)].append(txn)

        newly_recorded_count = 0
        for normalized_name, group in groups.items():
            vendor_id = await self._candidate_repository.find_existing_vendor_id(
                engagement_id=engagement_id, normalized_name=normalized_name
            )
            if vendor_id is None:
                vendor_id = str(uuid.uuid4())

            # The first transaction's raw spelling becomes the vendor's display name — arbitrary
            # but deterministic (group order follows `transactions`' own order), and re-running
            # resolution never changes an already-chosen display name since `vendor_id` is reused.
            vendor_name = group[0].vendor_name

            for txn in group:
                recorded = await self._candidate_repository.record_candidate_and_resolution(
                    engagement_id=engagement_id,
                    source_transaction_id=txn.transaction_id,
                    raw_name=txn.vendor_name,
                    normalized_name=normalized_name,
                    neo4j_entity_id=vendor_id,
                )
                if recorded:
                    newly_recorded_count += 1

                await self._graph_store.merge_vendor_and_transaction(
                    engagement_id=engagement_id,
                    vendor_id=vendor_id,
                    vendor_name=vendor_name,
                    normalized_name=normalized_name,
                    transaction_id=txn.transaction_id,
                    amount=txn.amount,
                    currency=txn.currency,
                    transaction_date=txn.transaction_date,
                )

        return newly_recorded_count

    async def list_vendors(self, *, engagement_id: str) -> list[VendorEntity]:
        return await self._graph_store.list_vendors(engagement_id=engagement_id)

    async def get_vendor_network(self, *, engagement_id: str, vendor_id: str) -> VendorNetwork:
        network = await self._graph_store.get_vendor_network(
            engagement_id=engagement_id, vendor_id=vendor_id
        )
        if network is None:
            # Deliberately the same message shape whether the vendor id doesn't exist at all or
            # belongs to another engagement — the two must be indistinguishable to the caller,
            # the same "not found, not forbidden" behavior Postgres RLS produces elsewhere in this
            # codebase (see neo4j_graph_store.py's module docstring).
            raise NotFoundError(f"Vendor {vendor_id} not found.")
        return network
