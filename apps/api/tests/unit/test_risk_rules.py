"""Unit tests for the rule engine — pure functions over plain data, no I/O."""

from __future__ import annotations

import math
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal

from auditmind_api.risk.application.rules import (
    detect_benford_deviation,
    detect_duplicate_payments,
    detect_threshold_and_round_dollar,
)
from auditmind_api.risk.domain.entities import AnomalySeverity, AnomalyType, Transaction


def make_transaction(
    *,
    amount: str,
    transaction_date: date = date(2026, 1, 1),
    vendor_name: str | None = "Acme Corp",
    engagement_id: str = "eng-1",
) -> Transaction:
    raw_payload: dict[str, object] = {"amount": amount}
    if vendor_name is not None:
        raw_payload["vendor_name"] = vendor_name
    return Transaction(
        id=str(uuid.uuid4()),
        engagement_id=engagement_id,
        source_system="test",
        amount=Decimal(amount),
        currency="USD",
        transaction_date=transaction_date,
        raw_payload=raw_payload,
        created_by="user-1",
        created_at=datetime.now(UTC),
    )


def _benford_conforming_amounts(n: int) -> list[str]:
    """Generates amounts whose leading digits exactly follow Benford's expected proportions —
    used to prove the detector does *not* fire on genuinely conforming data."""
    amounts: list[str] = []
    for digit in range(1, 10):
        expected_proportion = math.log10(1 + 1 / digit)
        count = round(expected_proportion * n)
        amounts.extend(f"{digit}00.00" for _ in range(count))
    return amounts


def test_benford_deviation_not_flagged_below_minimum_sample_size() -> None:
    transactions = [make_transaction(amount="100.00") for _ in range(10)]

    result = detect_benford_deviation(transactions, minimum_sample_size=50)

    assert result is None


def test_benford_deviation_not_flagged_for_conforming_distribution() -> None:
    transactions = [make_transaction(amount=a) for a in _benford_conforming_amounts(300)]

    result = detect_benford_deviation(transactions, minimum_sample_size=50)

    assert result is None


def test_benford_deviation_flagged_when_every_amount_shares_the_same_leading_digit() -> None:
    """The most extreme possible deviation from Benford's Law: every single transaction starts
    with the same digit — real, independently-generated financial data essentially never looks
    like this."""
    transactions = [make_transaction(amount="900.00") for _ in range(60)]

    result = detect_benford_deviation(transactions, minimum_sample_size=50)

    assert result is not None
    assert result.anomaly_type == AnomalyType.BENFORD_DEVIATION
    assert result.severity == AnomalySeverity.HIGH
    assert result.transaction_id is None  # population-level, not tied to one transaction
    assert result.details["sample_size"] == 60


def test_benford_deviation_ignores_zero_amount_transactions() -> None:
    transactions = [make_transaction(amount="0.00") for _ in range(60)]

    result = detect_benford_deviation(transactions, minimum_sample_size=50)

    assert result is None  # zero has no leading digit; sample effectively empty


def test_duplicate_payment_flags_same_vendor_same_amount_within_proximity() -> None:
    first = make_transaction(
        amount="500.00", transaction_date=date(2026, 1, 1), vendor_name="Acme Corp"
    )
    second = make_transaction(
        amount="500.00", transaction_date=date(2026, 1, 2), vendor_name="acme corp  "
    )

    candidates = detect_duplicate_payments([first, second], date_proximity_days=3)

    assert len(candidates) == 1
    assert candidates[0].anomaly_type == AnomalyType.DUPLICATE_PAYMENT
    assert candidates[0].transaction_id == second.id
    assert candidates[0].details["duplicate_of_transaction_id"] == first.id


def test_duplicate_payment_not_flagged_outside_date_proximity() -> None:
    first = make_transaction(amount="500.00", transaction_date=date(2026, 1, 1))
    second = make_transaction(amount="500.00", transaction_date=date(2026, 2, 1))

    candidates = detect_duplicate_payments([first, second], date_proximity_days=3)

    assert candidates == []


def test_duplicate_payment_not_flagged_for_different_vendors() -> None:
    first = make_transaction(amount="500.00", vendor_name="Acme Corp")
    second = make_transaction(amount="500.00", vendor_name="Globex Inc")

    candidates = detect_duplicate_payments([first, second], date_proximity_days=3)

    assert candidates == []


def test_duplicate_payment_ignores_transactions_with_no_vendor_name() -> None:
    first = make_transaction(amount="500.00", vendor_name=None)
    second = make_transaction(amount="500.00", vendor_name=None)

    candidates = detect_duplicate_payments([first, second], date_proximity_days=3)

    assert candidates == []


def test_threshold_structuring_flags_amount_just_under_threshold() -> None:
    transaction = make_transaction(amount="9800.00")

    candidates = detect_threshold_and_round_dollar(
        [transaction], structuring_threshold=Decimal("10000")
    )

    structuring = [c for c in candidates if c.anomaly_type == AnomalyType.THRESHOLD_STRUCTURING]
    assert len(structuring) == 1
    assert structuring[0].transaction_id == transaction.id


def test_threshold_structuring_not_flagged_well_below_threshold() -> None:
    transaction = make_transaction(amount="5000.00")

    candidates = detect_threshold_and_round_dollar(
        [transaction], structuring_threshold=Decimal("10000")
    )

    assert [c for c in candidates if c.anomaly_type == AnomalyType.THRESHOLD_STRUCTURING] == []


def test_round_dollar_flags_exact_thousand_multiple() -> None:
    transaction = make_transaction(amount="5000.00")

    candidates = detect_threshold_and_round_dollar(
        [transaction], structuring_threshold=Decimal("10000")
    )

    round_dollar = [c for c in candidates if c.anomaly_type == AnomalyType.ROUND_DOLLAR]
    assert len(round_dollar) == 1
    assert round_dollar[0].severity == AnomalySeverity.LOW


def test_round_dollar_not_flagged_for_non_round_amount() -> None:
    transaction = make_transaction(amount="5432.17")

    candidates = detect_threshold_and_round_dollar(
        [transaction], structuring_threshold=Decimal("10000")
    )

    assert [c for c in candidates if c.anomaly_type == AnomalyType.ROUND_DOLLAR] == []


def test_a_transaction_can_trigger_both_structuring_and_round_dollar() -> None:
    """9000 is both a round-dollar amount and just under a 9500-95% structuring floor for a
    10000 threshold — a transaction legitimately earning two independent flags is not a bug."""
    transaction = make_transaction(amount="9500.00")

    candidates = detect_threshold_and_round_dollar(
        [transaction], structuring_threshold=Decimal("10000")
    )

    types = {c.anomaly_type for c in candidates}
    assert AnomalyType.THRESHOLD_STRUCTURING in types
    assert AnomalyType.ROUND_DOLLAR not in types  # 9500 is not a multiple of 1000 — sanity check


def test_leading_digit_distribution_helper_matches_expected_benford_shape() -> None:
    """Sanity-checks the test helper itself: a large conforming sample's observed distribution is
    close to Benford's expected proportions, so a false negative in the "not flagged" tests above
    isn't hiding a broken generator."""
    amounts = _benford_conforming_amounts(1000)
    leading_digits = [int(a[0]) for a in amounts]
    counts = Counter(leading_digits)
    for digit in range(1, 10):
        expected = math.log10(1 + 1 / digit)
        observed = counts.get(digit, 0) / len(leading_digits)
        assert abs(observed - expected) < 0.01
