"""Repository ports (Phase 3 §1) for the Risk & Anomaly Detection context."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from auditmind_api.risk.domain.entities import (
    Anomaly,
    AnomalyStatus,
    AnomalyType,
    RiskScore,
    Transaction,
)


class TransactionRepository(Protocol):
    async def bulk_create(self, transactions: list[Transaction]) -> list[Transaction]: ...

    async def list_for_engagement(self, engagement_id: str) -> list[Transaction]: ...


class AnomalyRepository(Protocol):
    async def bulk_create(self, anomalies: list[Anomaly]) -> list[Anomaly]: ...

    async def list_for_engagement(self, engagement_id: str) -> list[Anomaly]: ...

    async def get(self, anomaly_id: str) -> Anomaly | None: ...

    async def list_open_by_type_for_engagement(
        self, *, engagement_id: str, anomaly_type: AnomalyType
    ) -> list[Anomaly]:
        """Backs the scan's dedup check: never raise a second ``open`` anomaly of the same type for
        a transaction (or, for the population-level Benford check, for the engagement) that already
        has one outstanding — re-running a scan is idempotent with respect to what's still
        unreviewed, rather than flooding the queue with repeats of the same unreviewed finding."""
        ...

    async def update_disposition(
        self, *, anomaly_id: str, status: AnomalyStatus, reviewed_by: str, reviewed_at: datetime
    ) -> None: ...


class RiskScoreRepository(Protocol):
    async def bulk_upsert(self, scores: list[RiskScore]) -> list[RiskScore]:
        """Idempotent per ``(subject_type, subject_id, score_version)`` (Increment 10's migration
        constraint) — recomputing under the same model version refreshes the existing row rather
        than accumulating duplicates, since Isolation Forest and HDBSCAN are both
        population-relative and a score can legitimately change between runs."""
        ...

    async def list_for_engagement(self, engagement_id: str) -> list[RiskScore]: ...


class VendorCentralitySource(Protocol):
    """The one thing this context needs from ``kg``'s Neo4j graph — its own minimal protocol
    reading Neo4j directly (through the shared driver, not by importing ``kg``'s
    ``Neo4jGraphStore`` class), the same "define the shape of what you need, never the other
    context's own port or adapter" convention every cross-context read in this codebase already
    follows, applied here to Neo4j instead of Postgres for the first time."""

    async def get_vendor_transaction_counts(
        self, *, engagement_id: str
    ) -> dict[str, int]:
        """Returns ``normalized_vendor_name -> transaction count`` — the graph's degree centrality
        for a Vendor node (Phase 7 §6's "relational" feature family), keyed by normalized name
        since ``risk.transactions`` has no resolved vendor entity id of its own to join against
        (see Increment 05's own deferred note on exactly this gap). Empty if
        ``POST .../knowledge-graph/resolve`` was never run for this engagement — vendor
        resolution is this signal's prerequisite, not something it triggers itself."""
        ...


class AuditTrailRecorder(Protocol):
    """The one thing this context needs from ``audit_trail`` — its own minimal protocol, same
    reasoning as ``reporting.domain.ports.AuditTrailRecorder``, not a shared import between the two
    (Phase 3 §1's boundary applies between contexts, not just between layers)."""

    async def record(
        self,
        *,
        engagement_id: str,
        actor_id: str,
        action: str,
        subject_type: str,
        subject_id: str,
        before_state: dict[str, Any] | None,
        after_state: dict[str, Any] | None,
    ) -> None: ...
