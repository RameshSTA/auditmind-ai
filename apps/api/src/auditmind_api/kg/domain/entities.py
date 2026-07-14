"""Domain entities for the Knowledge Graph context.

Plain, framework-free dataclasses — the same convention every prior context established. Vendor
identity here is scoped to resolution from ``risk.transactions``' structured ``vendor_name``
field, not free-text NLP extraction from document chunks (see the migration's docstring for why).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


def normalize_vendor_name(raw_name: str) -> str:
    """Trimmed, Unicode-normalized (NFKC), case-folded — normalized exact-match, not fuzzy/edit-
    distance matching. The same "normalized exact-match only" scope the duplicate-payment check
    draws elsewhere (a genuine fuzzy-string library is a reasonable future addition, not required
    to catch the common case: the same vendor spelled identically but for case/whitespace across
    ERP records). Deliberately re-implemented here rather than imported from
    ``risk.application.rules._vendor_name`` — bounded contexts don't import each other's internals,
    and this is domain logic each context owns independently, not shared data.
    """
    return unicodedata.normalize("NFKC", raw_name).strip().casefold()


@dataclass(frozen=True)
class VendorTransactionSummary:
    """One transaction in a vendor's network — the detail view's line items."""

    transaction_id: str
    amount: Decimal
    currency: str
    transaction_date: date


@dataclass(frozen=True)
class VendorEntity:
    """A resolved vendor node — one row per canonical vendor identity, after merging every raw
    name variant that normalized to the same string. ``id`` is the stable ``neo4j_entity_id``
    (``kg.entity_resolution_map.neo4j_entity_id``), not a Postgres primary key; this context's
    read model is the graph, not the Postgres staging tables."""

    id: str
    engagement_id: str
    name: str
    normalized_name: str
    transaction_count: int
    # Grouped by currency rather than a single summed total — collapsing different currencies into
    # one number would silently produce a meaningless figure, not just an imprecise one.
    total_amount_by_currency: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class VendorNetwork:
    """A vendor plus every transaction paid to it — the "vendor 360" view the fraud-investigation
    persona needs (US: "show me everything related to this vendor")."""

    vendor: VendorEntity
    transactions: list[VendorTransactionSummary]
