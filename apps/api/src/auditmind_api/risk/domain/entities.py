"""Domain entities for the Risk & Anomaly Detection context.

Plain, framework-free dataclasses — no SQLAlchemy, no FastAPI, the same layering convention every
prior context established.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class AnomalyType(str, Enum):
    """The rule engine's three checks — the first, non-ML tier of the fraud-scoring ensemble.
    Isolation Forest / HDBSCAN / the weighted combiner are later, ML-dependent tiers of the same
    design."""

    BENFORD_DEVIATION = "benford_deviation"
    DUPLICATE_PAYMENT = "duplicate_payment"
    THRESHOLD_STRUCTURING = "threshold_structuring"
    ROUND_DOLLAR = "round_dollar"


class AnomalySeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyStatus(str, Enum):
    """``open`` / ``true_positive`` / ``false_positive`` — the schema's status column, feeding
    the reviewer-disposition requirement."""

    OPEN = "open"
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"


@dataclass(frozen=True)
class Transaction:
    id: str
    engagement_id: str
    source_system: str
    amount: Decimal
    currency: str
    transaction_date: date
    # Original ERP record, preserved verbatim for evidentiary traceability. The rule engine reads
    # a `vendor_name` key from here rather than a dedicated column — entity resolution
    # (`vendor_entity_id` pointing at `kg.entity_resolution_map`) needs Neo4j, which isn't wired
    # up in every environment; raw_payload is the only source of a vendor identifier until then.
    raw_payload: dict[str, Any]
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class Anomaly:
    id: str
    engagement_id: str
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    status: AnomalyStatus
    detected_at: datetime
    # Nullable: a Benford's Law deviation is a population-level verdict (run over a group of
    # transactions, not one), so it has no single transaction to attach to. Duplicate-payment and
    # threshold/round-dollar anomalies are always per-transaction and always set this.
    transaction_id: str | None = None
    # The specific evidence behind the flag (e.g. the Benford MAD score and digit distribution, or
    # the matched duplicate transaction's id) — feeds the "contributing factors" requirement, the
    # same explainability principle `risk.risk_scores.contributing_factors` exists for.
    details: dict[str, Any] = field(default_factory=dict)
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class RiskScore:
    """The weighted-linear risk combiner's output — one row per subject per model version.
    ``subject_type`` is polymorphic (transaction / vendor / control); currently only
    ``subject_type == "transaction"`` rows are ever produced."""

    id: str
    engagement_id: str
    subject_type: str
    subject_id: str
    score: Decimal
    score_version: str
    contributing_factors: dict[str, Any]
    computed_at: datetime
