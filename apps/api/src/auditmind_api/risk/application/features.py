"""Transaction-level feature engineering (Phase 7 §6) for the ML-dependent signal sources
(``ml_signals.py``). Pure functions over a list of transactions, no I/O, no framework import —
the same "pure computation, trivially unit-testable" shape ``rules.py`` already established, since
building a feature vector is deterministic computation over data already in hand, not a call to an
external system.

**Scoped deliberately smaller than Phase 7 §6's full feature table.** That table lists three
families: transaction-level, vendor-level (aggregated), and relational (from Neo4j). This module
builds only the transaction-level family, reusing signals the rule engine (``rules.py``) already
computes rather than re-deriving them — a round-dollar or near-threshold amount is exactly as
meaningful a feature to an Isolation Forest as it is a standalone rule. The vendor-level family
needs a "trailing N days" concept this codebase has no as-of-date model for yet (every transaction
currently imported is treated as "current," not dated relative to a scan time); the relational
family is ``ml_signals.py``'s graph-centrality sibling, not built here since it needs Neo4j I/O this
module deliberately has none of.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auditmind_api.risk.domain.entities import Transaction

_ROUND_DOLLAR_MULTIPLE = Decimal("1000")
_ROUND_DOLLAR_MINIMUM = Decimal("1000")


def normalized_vendor_name(transaction: Transaction) -> str | None:
    """Same normalization convention as ``rules.py``'s own ``_vendor_name`` and ``kg``'s
    ``normalize_vendor_name`` — trimmed, case-folded. Re-implemented here rather than imported
    from ``rules.py`` because feature-building and rule-evaluation are conceptually independent
    consumers of the same raw field, not because the logic itself needs to differ; kept identical
    on purpose so a feature and a rule agree on what counts as "the same vendor.\""""
    raw_name = transaction.raw_payload.get("vendor_name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    return raw_name.strip().casefold()


@dataclass(frozen=True)
class TransactionFeatures:
    """One row of the feature matrix ``ml_signals.py``'s Isolation Forest and HDBSCAN both consume
    — plain floats, since neither library accepts ``Decimal`` directly. Every feature is already
    scaled to a roughly comparable range (mostly `[0, 1]`) so no component dominates a
    distance-based algorithm (HDBSCAN) purely by having a larger native magnitude than the others.
    """

    transaction_id: str
    normalized_amount: float
    is_round_dollar: float
    is_near_threshold: float
    day_of_month_fraction: float
    deviation_from_vendor_average: float


def build_feature_matrix(
    transactions: list[Transaction], *, structuring_threshold: Decimal = Decimal("10000")
) -> list[TransactionFeatures]:
    """Builds one :class:`TransactionFeatures` row per transaction.

    ``normalized_amount`` is scaled against the *population's own* maximum amount (not a fixed
    currency-agnostic constant) — meaningful only relative to this engagement's own transaction
    population, the same "what's typical *here*" framing every other signal in this ensemble uses
    (Benford's Law and duplicate-payment matching are both population-relative, not
    absolute-threshold checks either, except the two Phase 7 §2 rules that are deliberately
    absolute-threshold by design: structuring and round-dollar).
    """
    if not transactions:
        return []

    max_amount = max((t.amount for t in transactions), default=Decimal("1")) or Decimal("1")
    structuring_floor = structuring_threshold * Decimal("0.95")

    # Keyed by (transaction_id, amount) rather than just amount — two transactions can
    # legitimately share the same vendor and amount (that's exactly what
    # ``detect_duplicate_payments`` looks for), so identifying "this transaction's own entry" by
    # value alone would silently exclude the wrong row whenever such a pair exists.
    vendor_totals: dict[str, list[tuple[str, Decimal]]] = {}
    for txn in transactions:
        vendor = normalized_vendor_name(txn)
        if vendor is not None:
            vendor_totals.setdefault(vendor, []).append((txn.id, txn.amount))

    features = []
    for txn in transactions:
        is_round_dollar = (
            1.0
            if txn.amount >= _ROUND_DOLLAR_MINIMUM and txn.amount % _ROUND_DOLLAR_MULTIPLE == 0
            else 0.0
        )
        is_near_threshold = (
            1.0 if structuring_floor <= txn.amount < structuring_threshold else 0.0
        )
        day_of_month_fraction = (txn.transaction_date.day - 1) / 30.0

        vendor = normalized_vendor_name(txn)
        deviation_from_vendor_average = 0.0
        if vendor is not None:
            entries = vendor_totals[vendor]
            other_amounts = [amount for txn_id, amount in entries if txn_id != txn.id]
            if not other_amounts:
                other_amounts = [amount for _, amount in entries]
            # `start=Decimal("0")` pins `sum`'s return type to `Decimal` — its stub otherwise
            # falls back to a `Decimal | Literal[0]` overload that mypy can't cleanly divide.
            vendor_average = sum(other_amounts, start=Decimal("0")) / len(other_amounts)
            if vendor_average != 0:
                deviation_from_vendor_average = float(
                    abs(txn.amount - vendor_average) / vendor_average
                )

        features.append(
            TransactionFeatures(
                transaction_id=txn.id,
                normalized_amount=float(txn.amount / max_amount),
                is_round_dollar=is_round_dollar,
                is_near_threshold=is_near_threshold,
                day_of_month_fraction=day_of_month_fraction,
                deviation_from_vendor_average=deviation_from_vendor_average,
            )
        )
    return features
