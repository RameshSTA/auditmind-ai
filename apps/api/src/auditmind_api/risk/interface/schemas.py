"""Request bodies for the Risk context's routes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TransactionRecord(BaseModel):
    """Extra fields (e.g. ``vendor_name``, used by the duplicate-payment rule — see
    ``risk/domain/entities.py``'s ``Transaction.raw_payload`` docstring) are preserved, not
    rejected — ``raw_payload`` exists specifically to keep the original ERP record verbatim."""

    model_config = ConfigDict(extra="allow")

    amount: Decimal
    currency: str = "USD"
    source_system: str = "manual_import"
    transaction_date: date


class ImportTransactionsRequest(BaseModel):
    transactions: list[TransactionRecord] = Field(min_length=1)


class ScanRequest(BaseModel):
    structuring_threshold: Decimal = Decimal("10000")


class DispositionAnomalyRequest(BaseModel):
    """Only the two terminal dispositions are accepted — ``open`` is a starting state, never a
    target a caller sets it back to."""

    status: Literal["true_positive", "false_positive"]


def transaction_record_to_service_input(record: TransactionRecord) -> dict[str, Any]:
    """Splits a validated request record into what ``RiskService.import_transactions`` needs:
    typed domain fields plus a JSON-safe ``raw_payload`` snapshot of exactly what was submitted."""
    return {
        "amount": record.amount,
        "currency": record.currency,
        "source_system": record.source_system,
        "transaction_date": record.transaction_date,
        "raw_payload": record.model_dump(mode="json"),
    }
