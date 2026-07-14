"""Unit tests for KnowledgeGraphService (application layer) — against in-memory fakes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from auditmind_api.kg.application.services import KnowledgeGraphService
from auditmind_api.kg.domain.entities import VendorEntity, VendorNetwork, normalize_vendor_name
from auditmind_api.kg.domain.ports import TransactionForResolution
from auditmind_api.shared.errors import NotFoundError


class FakeTransactionSource:
    def __init__(self, transactions: list[TransactionForResolution] | None = None) -> None:
        self.transactions = transactions or []

    async def list_transactions_with_vendor(
        self, *, engagement_id: str
    ) -> list[TransactionForResolution]:
        return self.transactions


class FakeEntityCandidateRepository:
    def __init__(self) -> None:
        # keyed by (engagement_id, normalized_name) -> vendor_id
        self._resolved: dict[tuple[str, str], str] = {}
        self._recorded_transactions: set[str] = set()
        self.record_calls: list[dict[str, str]] = []

    async def find_existing_vendor_id(
        self, *, engagement_id: str, normalized_name: str
    ) -> str | None:
        return self._resolved.get((engagement_id, normalized_name))

    async def record_candidate_and_resolution(
        self,
        *,
        engagement_id: str,
        source_transaction_id: str,
        raw_name: str,
        normalized_name: str,
        neo4j_entity_id: str,
    ) -> bool:
        self.record_calls.append(
            {
                "engagement_id": engagement_id,
                "source_transaction_id": source_transaction_id,
                "raw_name": raw_name,
                "normalized_name": normalized_name,
                "neo4j_entity_id": neo4j_entity_id,
            }
        )
        self._resolved[(engagement_id, normalized_name)] = neo4j_entity_id
        if source_transaction_id in self._recorded_transactions:
            return False
        self._recorded_transactions.add(source_transaction_id)
        return True


class FakeGraphStore:
    def __init__(self) -> None:
        self.ensure_constraints_calls = 0
        self.merge_calls: list[dict[str, object]] = []
        self.vendors: list[VendorEntity] = []
        self.networks: dict[str, VendorNetwork] = {}

    async def ensure_constraints(self) -> None:
        self.ensure_constraints_calls += 1

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
        self.merge_calls.append(
            {
                "engagement_id": engagement_id,
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "normalized_name": normalized_name,
                "transaction_id": transaction_id,
                "amount": amount,
                "currency": currency,
                "transaction_date": transaction_date,
            }
        )

    async def list_vendors(self, *, engagement_id: str) -> list[VendorEntity]:
        return self.vendors

    async def get_vendor_network(
        self, *, engagement_id: str, vendor_id: str
    ) -> VendorNetwork | None:
        return self.networks.get(vendor_id)


def _txn(
    transaction_id: str, vendor_name: str, amount: str = "100.00", currency: str = "USD"
) -> TransactionForResolution:
    return TransactionForResolution(
        transaction_id=transaction_id,
        vendor_name=vendor_name,
        amount=Decimal(amount),
        currency=currency,
        transaction_date=date(2026, 1, 1),
    )


def test_normalize_vendor_name_trims_and_case_folds() -> None:
    assert normalize_vendor_name("  Acme Corp  ") == "acme corp"
    assert normalize_vendor_name("ACME CORP") == "acme corp"


async def test_resolve_vendors_ensures_constraints_before_reading_transactions() -> None:
    graph_store = FakeGraphStore()
    service = KnowledgeGraphService(
        FakeTransactionSource(), FakeEntityCandidateRepository(), graph_store
    )

    await service.resolve_vendors(engagement_id="eng-1")

    assert graph_store.ensure_constraints_calls == 1


async def test_resolve_vendors_groups_identical_normalized_names_under_one_vendor_id() -> None:
    transactions = [
        _txn("t1", "Acme Corp"),
        _txn("t2", "acme corp"),
        _txn("t3", "  ACME CORP  "),
    ]
    graph_store = FakeGraphStore()
    service = KnowledgeGraphService(
        FakeTransactionSource(transactions), FakeEntityCandidateRepository(), graph_store
    )

    newly_resolved_count = await service.resolve_vendors(engagement_id="eng-1")

    assert newly_resolved_count == 3
    vendor_ids = {call["vendor_id"] for call in graph_store.merge_calls}
    assert len(vendor_ids) == 1  # all three variants resolved to the same graph identity


async def test_resolve_vendors_keeps_different_vendors_separate() -> None:
    transactions = [_txn("t1", "Acme Corp"), _txn("t2", "Globex Inc")]
    graph_store = FakeGraphStore()
    service = KnowledgeGraphService(
        FakeTransactionSource(transactions), FakeEntityCandidateRepository(), graph_store
    )

    await service.resolve_vendors(engagement_id="eng-1")

    vendor_ids = {call["vendor_id"] for call in graph_store.merge_calls}
    assert len(vendor_ids) == 2


async def test_resolve_vendors_reuses_existing_vendor_id_on_a_second_run() -> None:
    candidate_repository = FakeEntityCandidateRepository()
    graph_store = FakeGraphStore()
    first_run_source = FakeTransactionSource([_txn("t1", "Acme Corp")])
    service = KnowledgeGraphService(first_run_source, candidate_repository, graph_store)
    await service.resolve_vendors(engagement_id="eng-1")
    first_vendor_id = graph_store.merge_calls[0]["vendor_id"]

    second_run_source = FakeTransactionSource([_txn("t1", "Acme Corp"), _txn("t2", "acme corp")])
    service = KnowledgeGraphService(second_run_source, candidate_repository, graph_store)
    newly_resolved_count = await service.resolve_vendors(engagement_id="eng-1")

    second_run_vendor_ids = {
        call["vendor_id"] for call in graph_store.merge_calls[1:]
    }
    assert second_run_vendor_ids == {first_vendor_id}
    # t1 was already recorded in the first run — only t2 is genuinely new.
    assert newly_resolved_count == 1
    # But both t1 and t2 still get their graph edge re-asserted (self-healing, see the service's
    # docstring) — merge_calls has 1 (first run) + 2 (second run, both transactions) = 3 entries.
    assert len(graph_store.merge_calls) == 3


async def test_resolve_vendors_ignores_transactions_the_source_already_filtered_out() -> None:
    """The port contract is that `TransactionSource` only returns transactions with a vendor name
    — this test documents that the service does no additional filtering of its own."""
    service = KnowledgeGraphService(
        FakeTransactionSource([]), FakeEntityCandidateRepository(), FakeGraphStore()
    )

    newly_resolved_count = await service.resolve_vendors(engagement_id="eng-1")

    assert newly_resolved_count == 0


async def test_list_vendors_delegates_to_the_graph_store() -> None:
    expected = [
        VendorEntity(
            id="v1",
            engagement_id="eng-1",
            name="Acme Corp",
            normalized_name="acme corp",
            transaction_count=2,
            total_amount_by_currency={"USD": Decimal("200.00")},
        )
    ]
    graph_store = FakeGraphStore()
    graph_store.vendors = expected
    service = KnowledgeGraphService(
        FakeTransactionSource(), FakeEntityCandidateRepository(), graph_store
    )

    result = await service.list_vendors(engagement_id="eng-1")

    assert result == expected


async def test_get_vendor_network_returns_the_network_when_found() -> None:
    vendor = VendorEntity(
        id="v1",
        engagement_id="eng-1",
        name="Acme Corp",
        normalized_name="acme corp",
        transaction_count=0,
        total_amount_by_currency={},
    )
    network = VendorNetwork(vendor=vendor, transactions=[])
    graph_store = FakeGraphStore()
    graph_store.networks["v1"] = network
    service = KnowledgeGraphService(
        FakeTransactionSource(), FakeEntityCandidateRepository(), graph_store
    )

    result = await service.get_vendor_network(engagement_id="eng-1", vendor_id="v1")

    assert result == network


async def test_get_vendor_network_raises_not_found_when_absent() -> None:
    service = KnowledgeGraphService(
        FakeTransactionSource(), FakeEntityCandidateRepository(), FakeGraphStore()
    )

    with pytest.raises(NotFoundError):
        await service.get_vendor_network(engagement_id="eng-1", vendor_id="missing")
