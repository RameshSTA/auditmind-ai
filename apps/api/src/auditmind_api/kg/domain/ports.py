"""Adapter ports (Phase 3 §1) for the Knowledge Graph context.

``TransactionSource`` is the one thing this context needs from ``risk``: a way to read transactions
that carry a vendor name, to resolve into graph entities. Its own minimal protocol, not a shared
import — the same ``ChunkLookup``/``AuditTrailRecorder``/``ChunkTextSource`` convention every prior
cross-context read in this codebase establishes.

``EntityCandidateRepository`` and ``GraphStore`` are this context's own concerns — the first is the
Postgres "bridge" table pair this context owns (``kg.entity_candidates``,
``kg.entity_resolution_map``, Phase 4 §4), the second is Neo4j itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from auditmind_api.kg.domain.entities import VendorEntity, VendorNetwork


@dataclass(frozen=True)
class TransactionForResolution:
    """The minimal shape this context needs from a transaction it does not own — an id to key the
    resulting candidate by, and just enough to resolve + graph a vendor relationship. Deliberately
    not ``risk``'s own ``Transaction`` entity (Phase 3 §1 — a bounded context never imports
    another's domain model)."""

    transaction_id: str
    vendor_name: str
    amount: Decimal
    currency: str
    transaction_date: date


class TransactionSource(Protocol):
    async def list_transactions_with_vendor(
        self, *, engagement_id: str
    ) -> list[TransactionForResolution]: ...


class EntityCandidateRepository(Protocol):
    async def find_existing_vendor_id(
        self, *, engagement_id: str, normalized_name: str
    ) -> str | None:
        """The stable ``neo4j_entity_id`` a prior resolution run already assigned to this
        normalized name within this engagement, if any — so re-running resolution reuses the same
        vendor identity instead of minting a new one every time."""
        ...

    async def record_candidate_and_resolution(
        self,
        *,
        engagement_id: str,
        source_transaction_id: str,
        raw_name: str,
        normalized_name: str,
        neo4j_entity_id: str,
    ) -> bool:
        """Idempotent: a candidate already recorded for this exact transaction is a no-op. Returns
        whether a new candidate was actually recorded (for the caller's own "how many did this run
        resolve" count) — the Neo4j write this accompanies is idempotent regardless either way
        (see ``GraphStore.merge_vendor_and_transaction``), so this return value is purely
        informational, never a precondition for whether the graph gets written."""
        ...


class GraphStore(Protocol):
    async def ensure_constraints(self) -> None:
        """Idempotent schema setup — Neo4j's closest equivalent to Alembic's ``upgrade head``.
        Deliberately not run at process startup (unlike Postgres migrations): this codebase's
        ``/healthz`` is documented to never check downstream dependencies, and blocking API
        startup on Neo4j being reachable would be exactly that check in disguise. Called instead
        from ``KnowledgeGraphService.resolve_vendors``, once per call, the same lazy-setup
        discipline Increment 08's BGE-M3 model load already established."""
        ...

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
        """Upserts a Vendor node, a Transaction node, and a PAID edge between them — safe to call
        repeatedly with the same arguments (Cypher ``MERGE``, not ``CREATE``)."""
        ...

    async def list_vendors(self, *, engagement_id: str) -> list[VendorEntity]: ...

    async def get_vendor_network(
        self, *, engagement_id: str, vendor_id: str
    ) -> VendorNetwork | None: ...
