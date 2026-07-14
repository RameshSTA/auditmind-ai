"""Domain entities for the Investigations context.

Plain, framework-free dataclasses — no SQLAlchemy, no FastAPI (Phase 3 §1 layering), the same
convention every prior context established.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class InvestigationStatus(str, Enum):
    """An investigation is open until a human closes it with a documented conclusion — the same
    "no path backward" disposition shape ``reporting.FindingStatus`` established: once closed, a
    correction means opening a new investigation, not mutating the record of what was decided."""

    OPEN = "open"
    CLOSED = "closed"


class InvestigationSubjectType(str, Enum):
    """What an ``InvestigationItem`` can point at — the same polymorphic ``subject_type`` shape
    ``risk.risk_scores`` established, scoped to the three entity types an investigation actually
    triages: a finding, an anomaly, or the raw transaction behind either of those."""

    FINDING = "finding"
    ANOMALY = "anomaly"
    TRANSACTION = "transaction"


@dataclass(frozen=True)
class Investigation:
    id: str
    engagement_id: str
    title: str
    description: str
    status: InvestigationStatus
    created_by: str
    created_at: datetime
    closed_by: str | None = None
    closed_at: datetime | None = None
    conclusion: str | None = None


@dataclass(frozen=True)
class InvestigationItem:
    """One piece of evidence pulled into an investigation's working set — a finding, an anomaly,
    or a transaction, with an optional note on why it's relevant. This is the case-building
    mechanism: an investigation is defined by which items an investigator has gathered under it,
    not by any computation of its own."""

    id: str
    investigation_id: str
    engagement_id: str
    subject_type: InvestigationSubjectType
    subject_id: str
    added_by: str
    added_at: datetime
    note: str | None = None
