"""Repository and adapter ports (Phase 3 §1) for the Investigations context.

The application layer depends on these interfaces; infrastructure provides the only
implementations. No SQLAlchemy import belongs here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from auditmind_api.investigations.domain.entities import Investigation, InvestigationItem


class InvestigationRepository(Protocol):
    async def create(self, investigation: Investigation) -> Investigation: ...

    async def get(self, investigation_id: str) -> Investigation | None: ...

    async def list_for_engagement(self, engagement_id: str) -> list[Investigation]: ...

    async def close(
        self,
        *,
        investigation_id: str,
        closed_by: str,
        closed_at: datetime,
        conclusion: str,
    ) -> None: ...


class InvestigationItemRepository(Protocol):
    async def add(self, item: InvestigationItem) -> InvestigationItem: ...

    async def remove(self, item_id: str) -> None: ...

    async def list_for_investigation(self, investigation_id: str) -> list[InvestigationItem]: ...


class InvestigationSubjectLookup(Protocol):
    """The one thing this context needs from ``reporting``/``risk``: which engagement a candidate
    item (a finding, anomaly, or transaction) belongs to. Deliberately its own minimal protocol
    rather than importing another context's port (Phase 3 §1's boundary applies between contexts,
    not just between layers) — the same reasoning ``reporting``'s ``ChunkLookup`` documents.

    Row-Level Security on ``reporting.findings`` / ``risk.anomalies`` / ``risk.transactions``
    already stops a user from looking up a subject in an engagement they have no membership in at
    all. What it does *not* stop is a user who is legitimately a member of *two* engagements
    adding an item from engagement B to an investigation that belongs to engagement A — both reads
    are individually authorized, so only an explicit "does this subject's engagement match this
    investigation's engagement" check, performed before insert, prevents evidence from crossing
    engagement boundaries.
    """

    async def get_engagement_id(self, subject_type: str, subject_id: str) -> str | None: ...


class AuditTrailRecorder(Protocol):
    """The one thing this context needs from ``audit_trail``: a way to record that something
    happened. Its own minimal protocol, not a shared import — same reasoning as
    ``InvestigationSubjectLookup``."""

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
