"""Adapter for the ``TransactionSource`` port: reads ``risk.transactions`` without importing
``risk``'s ORM model (same raw-SQL boundary convention as ``reporting``'s ``chunk_lookup.py`` and
``retrieval``'s ``chunk_text_source.py``).

Extracts ``vendor_name`` from ``raw_payload`` (a JSONB column ``risk`` owns) via Postgres's
``->>`` operator — the same key ``risk/application/rules.py``'s duplicate-payment check already
reads, per that module's own documented convention (``Transaction.raw_payload``'s docstring).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.kg.domain.ports import TransactionForResolution


class PostgresTransactionSource:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_transactions_with_vendor(
        self, *, engagement_id: str
    ) -> list[TransactionForResolution]:
        result = await self._session.execute(
            text(
                "SELECT id, raw_payload ->> 'vendor_name' AS vendor_name, amount, currency, "
                "       transaction_date "
                "FROM risk.transactions "
                "WHERE engagement_id = :engagement_id "
                "  AND trim(raw_payload ->> 'vendor_name') IS NOT NULL "
                "  AND trim(raw_payload ->> 'vendor_name') != ''"
            ),
            {"engagement_id": engagement_id},
        )
        return [
            TransactionForResolution(
                transaction_id=str(row.id),
                vendor_name=row.vendor_name,
                amount=row.amount,
                currency=row.currency,
                transaction_date=row.transaction_date,
            )
            for row in result.all()
        ]
