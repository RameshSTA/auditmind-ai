"""The rule engine — pure functions over a list of transactions, no I/O, no framework import.
Each returns :class:`AnomalyCandidate` values; ``RiskService`` is what turns candidates into
persisted ``Anomaly`` rows, so these functions stay trivially unit-testable against plain data.

This is deliberately only the non-ML tier of the fraud-scoring ensemble: Isolation Forest,
HDBSCAN cohort clustering, and the weighted-linear risk combiner need a materially larger
transaction population and are handled separately.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from auditmind_api.risk.domain.entities import AnomalySeverity, AnomalyType, Transaction

# Nigrini's Mean Absolute Deviation conformity bands for the first-digit Benford test (Nigrini,
# "Benford's Law: Applications for Forensic Accounting, Auditing, and Fraud Detection", 2012) —
# an established forensic-accounting standard, not an arbitrary threshold invented for this
# codebase.
_MAD_NONCONFORMITY = Decimal("0.015")
_MAD_MARGINAL = Decimal("0.012")

# Nigrini's own guidance favors much larger samples (300+) for a reliable first-digit test; this
# accepts a lower floor for a first pass over what a single audit engagement plausibly has on
# hand early on, at the cost of more false positives/negatives on small populations — a
# documented simplification, not a claim of forensic rigor at n=50.
_BENFORD_MINIMUM_SAMPLE_SIZE = 50

_ROUND_DOLLAR_MULTIPLE = Decimal("1000")
_ROUND_DOLLAR_MINIMUM = Decimal("1000")


@dataclass(frozen=True)
class AnomalyCandidate:
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    transaction_id: str | None
    details: dict[str, Any] = field(default_factory=dict)


def _leading_digit(amount: Decimal) -> int | None:
    magnitude = abs(amount)
    if magnitude == 0:
        return None
    digits = format(magnitude.normalize(), "f").lstrip("0.")
    for ch in digits:
        if ch.isdigit() and ch != "0":
            return int(ch)
    return None


def detect_benford_deviation(
    transactions: list[Transaction], *, minimum_sample_size: int = _BENFORD_MINIMUM_SAMPLE_SIZE
) -> AnomalyCandidate | None:
    """Population-level check: does this engagement's transaction amounts' leading-digit
    distribution conform to Benford's Law? A significant deviation is characteristic of fabricated
    or manually adjusted figures — real transaction data drawn from many independent processes
    tends to follow Benford's distribution; manually typed or fabricated numbers tend not to.
    """
    leading_digits = [d for t in transactions if (d := _leading_digit(t.amount)) is not None]
    if len(leading_digits) < minimum_sample_size:
        return None

    n = len(leading_digits)
    counts = Counter(leading_digits)
    mad = sum(
        abs(Decimal(counts.get(d, 0)) / n - Decimal(str(math.log10(1 + 1 / d))))
        for d in range(1, 10)
    ) / 9

    if mad < _MAD_MARGINAL:
        return None

    severity = AnomalySeverity.HIGH if mad >= _MAD_NONCONFORMITY else AnomalySeverity.MEDIUM
    return AnomalyCandidate(
        anomaly_type=AnomalyType.BENFORD_DEVIATION,
        severity=severity,
        transaction_id=None,
        details={
            "sample_size": n,
            "mean_absolute_deviation": str(mad),
            "observed_distribution": {str(d): counts.get(d, 0) for d in range(1, 10)},
        },
    )


def _vendor_name(transaction: Transaction) -> str | None:
    raw_name = transaction.raw_payload.get("vendor_name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    return raw_name.strip().casefold()


def detect_duplicate_payments(
    transactions: list[Transaction], *, date_proximity_days: int = 3
) -> list[AnomalyCandidate]:
    """Fuzzy match on amount + vendor + date proximity.

    "Fuzzy" is scoped here to normalized (trimmed, case-folded) exact string equality on vendor
    name, not edit-distance matching — a genuine fuzzy-string library is a reasonable future
    addition but not required to catch the common case this check targets: the same invoice paid
    twice under identical vendor naming.
    """
    candidates: list[AnomalyCandidate] = []
    by_vendor_and_amount: dict[tuple[str, Decimal], list[Transaction]] = {}
    for txn in transactions:
        vendor = _vendor_name(txn)
        if vendor is None:
            continue
        by_vendor_and_amount.setdefault((vendor, txn.amount), []).append(txn)

    for group in by_vendor_and_amount.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda t: t.transaction_date)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            days_apart = (later.transaction_date - earlier.transaction_date).days
            if days_apart <= date_proximity_days:
                candidates.append(
                    AnomalyCandidate(
                        anomaly_type=AnomalyType.DUPLICATE_PAYMENT,
                        severity=(
                            AnomalySeverity.HIGH if days_apart == 0 else AnomalySeverity.MEDIUM
                        ),
                        transaction_id=later.id,
                        details={
                            "duplicate_of_transaction_id": earlier.id,
                            "amount": str(later.amount),
                            "days_apart": days_apart,
                        },
                    )
                )
    return candidates


def detect_threshold_and_round_dollar(
    transactions: list[Transaction], *, structuring_threshold: Decimal = Decimal("10000")
) -> list[AnomalyCandidate]:
    """Two independent per-transaction checks:

    - **Structuring**: an amount just under an approval/reporting threshold — the classic pattern
      of splitting or sizing a payment specifically to stay under a control that would otherwise
      trigger extra scrutiny. Flagged when the amount falls in the top 5% of the threshold's range.
    - **Round-dollar**: a suspiciously round amount (an exact multiple of $1,000) — legitimate
      transactions are rarely exactly round; manually entered or fabricated figures often are.
    """
    candidates: list[AnomalyCandidate] = []
    structuring_floor = structuring_threshold * Decimal("0.95")

    for txn in transactions:
        if structuring_floor <= txn.amount < structuring_threshold:
            candidates.append(
                AnomalyCandidate(
                    anomaly_type=AnomalyType.THRESHOLD_STRUCTURING,
                    severity=AnomalySeverity.HIGH,
                    transaction_id=txn.id,
                    details={
                        "amount": str(txn.amount),
                        "threshold": str(structuring_threshold),
                    },
                )
            )
        if txn.amount >= _ROUND_DOLLAR_MINIMUM and txn.amount % _ROUND_DOLLAR_MULTIPLE == 0:
            candidates.append(
                AnomalyCandidate(
                    anomaly_type=AnomalyType.ROUND_DOLLAR,
                    severity=AnomalySeverity.LOW,
                    transaction_id=txn.id,
                    details={"amount": str(txn.amount)},
                )
            )
    return candidates
