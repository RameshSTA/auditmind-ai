"""Domain entities for the Reporting & Audit Trail context.

Plain, framework-free dataclasses — no SQLAlchemy, no FastAPI, the same layering convention
``identity`` and ``ingestion`` already established.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class FindingSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    """``draft`` / ``confirmed`` / ``rejected``.

    Every finding starts ``DRAFT`` and transitions exactly once, to either ``CONFIRMED`` or
    ``REJECTED`` — the mandatory human sign-off gate before any finding is treated as audit-ready.
    There is deliberately no path back to ``DRAFT``: a rejected or confirmed finding is a disposed
    one, and correcting it means creating a new finding, not mutating the record of what was
    decided (the same "the record only ever grows" discipline ``audit_trail.events`` follows,
    applied here to the disposition itself).
    """

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Finding:
    id: str
    engagement_id: str
    title: str
    description: str
    severity: FindingSeverity
    status: FindingStatus
    created_by: str
    created_at: datetime
    control_id: str | None = None
    disposition_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class FindingEvidence:
    """One citation: a finding, a chunk it draws on, and the exact claim that chunk supports.

    This junction table *is* the citation graph — a finding without at least one
    ``FindingEvidence`` row is an unsupported claim, which is exactly what the platform's
    "citation-only answers" principle exists to prevent from being presented as fact.
    """

    id: str
    finding_id: str
    engagement_id: str
    chunk_id: str
    citation_text: str
    created_at: datetime


@dataclass(frozen=True)
class Report:
    id: str
    engagement_id: str
    version: int
    generated_by: str
    generated_at: datetime
    finding_ids: tuple[str, ...]
    body_markdown: str
    exported_uri: str | None = None
