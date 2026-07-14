"""Repository and adapter ports (Phase 3 §1) for the Reporting & Audit Trail context.

The application layer depends on these interfaces; infrastructure provides the only
implementations. No SQLAlchemy import belongs here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from auditmind_api.reporting.domain.entities import Finding, FindingEvidence, FindingStatus, Report


class FindingRepository(Protocol):
    async def create(self, finding: Finding) -> Finding: ...

    async def get(self, finding_id: str) -> Finding | None: ...

    async def list_for_engagement(self, engagement_id: str) -> list[Finding]: ...

    async def list_confirmed_for_engagement(self, engagement_id: str) -> list[Finding]:
        """Only ``CONFIRMED`` findings — the sole input a report is ever compiled from (Phase 1
        AC-05: "no report content sourced from an unconfirmed AI draft"). Filtering here, in the
        repository the report-generation path actually calls, makes it structurally impossible for
        a caller to assemble a report from anything else, rather than relying on every call site to
        remember to filter correctly."""
        ...

    async def update_disposition(
        self,
        *,
        finding_id: str,
        status: FindingStatus,
        reviewed_by: str,
        reviewed_at: datetime,
        disposition_reason: str | None,
    ) -> None: ...


class FindingEvidenceRepository(Protocol):
    async def create(self, evidence: FindingEvidence) -> FindingEvidence: ...

    async def list_for_finding(self, finding_id: str) -> list[FindingEvidence]: ...


class ReportRepository(Protocol):
    async def create(self, report: Report) -> Report: ...

    async def get(self, report_id: str) -> Report | None: ...

    async def list_for_engagement(self, engagement_id: str) -> list[Report]: ...

    async def next_version_for_engagement(self, engagement_id: str) -> int:
        """Reports are versioned per engagement (Phase 4 §1) — this returns the version number the
        *next* report for this engagement should use, computed from what already exists rather than
        left to the caller to track."""
        ...

    async def set_exported_uri(self, *, report_id: str, exported_uri: str) -> None:
        """Records where a generated PDF landed, once — a report's ``body_markdown``/
        ``finding_ids`` snapshot never changes after creation, but ``exported_uri`` legitimately
        does (null until the first export, then stable): the one field on this entity that's
        write-once-after-create rather than write-once-at-create."""
        ...


class ReportFileStorage(Protocol):
    """Where an exported report PDF's bytes live — its own minimal port, not a reuse of
    ``ingestion``'s ``BlobStorage`` (Phase 3 §1: a context defines the shape of what it needs,
    never imports another context's infrastructure class directly). A real Azure Blob Storage
    adapter later is a new file behind this same protocol, exactly like ``ingestion``'s own port.
    """

    async def put(self, *, engagement_id: str, report_id: str, content: bytes) -> str: ...

    async def get(self, storage_uri: str) -> bytes: ...


class ChunkLookup(Protocol):
    """The one thing this context needs from ``ingestion``: which engagement a chunk belongs to.

    Deliberately its own minimal protocol rather than importing ``ingestion.domain.ports`` directly
    — each bounded context defines the shape of what it needs from another context, not the other
    context's own port (Phase 3 §1's boundary applies between contexts, not just between layers).

    This is what makes ``FindingEvidence.chunk_id`` safe to accept from a caller. Row-Level Security
    on ``ingestion.chunks`` already stops a user from looking up a chunk in an engagement they have
    no membership in at all (the lookup itself returns nothing). What RLS does *not* stop is a user
    who is legitimately a member of *two* engagements citing a chunk from engagement B on a finding
    that belongs to engagement A — both reads are individually authorized, so only an explicit
    "does this chunk's engagement match this finding's engagement" check, performed here before
    insert, prevents evidence from crossing engagement boundaries.
    """

    async def get_engagement_id(self, chunk_id: str) -> str | None: ...


class AuditTrailRecorder(Protocol):
    """The one thing this context needs from ``audit_trail``: a way to record that something
    happened. Its own minimal protocol, not a shared import — same reasoning as ``ChunkLookup``.

    A finding's disposition (confirm/reject) is exactly the kind of event AC-04 exists to make
    permanently reconstructible: not just "the finding is now confirmed" (the row's current state
    already shows that) but "user X confirmed it at time Y, and here's what it looked like
    before" — the append-only history a mutable ``reviewed_by``/``reviewed_at`` column alone
    cannot reconstruct once a second disposition (were one ever allowed) overwrote the first.
    """

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
