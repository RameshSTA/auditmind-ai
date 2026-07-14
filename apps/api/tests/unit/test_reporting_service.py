"""Unit tests for ReportingService (Phase 3 §1 application layer) — against in-memory fakes, no
database involved."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from auditmind_api.reporting.application.exceptions import (
    ChunkEngagementMismatchError,
    ChunkNotFoundError,
    InvalidFindingTransitionError,
    MissingDispositionReasonError,
)
from auditmind_api.reporting.application.services import ReportingService
from auditmind_api.reporting.domain.entities import (
    Finding,
    FindingEvidence,
    FindingSeverity,
    FindingStatus,
    Report,
)
from auditmind_api.shared.errors import NotFoundError


class FakeFindingRepository:
    def __init__(self) -> None:
        self.findings: dict[str, Finding] = {}

    async def create(self, finding: Finding) -> Finding:
        self.findings[finding.id] = finding
        return finding

    async def get(self, finding_id: str) -> Finding | None:
        return self.findings.get(finding_id)

    async def list_for_engagement(self, engagement_id: str) -> list[Finding]:
        return [f for f in self.findings.values() if f.engagement_id == engagement_id]

    async def list_confirmed_for_engagement(self, engagement_id: str) -> list[Finding]:
        return [
            f
            for f in self.findings.values()
            if f.engagement_id == engagement_id and f.status == FindingStatus.CONFIRMED
        ]

    async def update_disposition(
        self,
        *,
        finding_id: str,
        status: FindingStatus,
        reviewed_by: str,
        reviewed_at: datetime,
        disposition_reason: str | None,
    ) -> None:
        existing = self.findings[finding_id]
        self.findings[finding_id] = Finding(
            id=existing.id,
            engagement_id=existing.engagement_id,
            title=existing.title,
            description=existing.description,
            severity=existing.severity,
            status=status,
            control_id=existing.control_id,
            created_by=existing.created_by,
            created_at=existing.created_at,
            disposition_reason=disposition_reason,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )


class FakeFindingEvidenceRepository:
    def __init__(self) -> None:
        self.evidence: list[FindingEvidence] = []

    async def create(self, evidence: FindingEvidence) -> FindingEvidence:
        self.evidence.append(evidence)
        return evidence

    async def list_for_finding(self, finding_id: str) -> list[FindingEvidence]:
        return [e for e in self.evidence if e.finding_id == finding_id]


class FakeReportRepository:
    def __init__(self) -> None:
        self.reports: dict[str, Report] = {}

    async def create(self, report: Report) -> Report:
        self.reports[report.id] = report
        return report

    async def get(self, report_id: str) -> Report | None:
        return self.reports.get(report_id)

    async def list_for_engagement(self, engagement_id: str) -> list[Report]:
        return [r for r in self.reports.values() if r.engagement_id == engagement_id]

    async def next_version_for_engagement(self, engagement_id: str) -> int:
        existing = [r for r in self.reports.values() if r.engagement_id == engagement_id]
        return (max((r.version for r in existing), default=0)) + 1

    async def set_exported_uri(self, *, report_id: str, exported_uri: str) -> None:
        existing = self.reports[report_id]
        self.reports[report_id] = Report(
            id=existing.id,
            engagement_id=existing.engagement_id,
            version=existing.version,
            generated_by=existing.generated_by,
            generated_at=existing.generated_at,
            finding_ids=existing.finding_ids,
            body_markdown=existing.body_markdown,
            exported_uri=exported_uri,
        )


class FakeReportFileStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(self, *, engagement_id: str, report_id: str, content: bytes) -> str:
        uri = f"{engagement_id}/{report_id}.pdf"
        self.files[uri] = content
        return uri

    async def get(self, storage_uri: str) -> bytes:
        return self.files[storage_uri]


class FakeChunkLookup:
    def __init__(self, engagement_by_chunk: dict[str, str] | None = None) -> None:
        self.engagement_by_chunk = engagement_by_chunk or {}

    async def get_engagement_id(self, chunk_id: str) -> str | None:
        return self.engagement_by_chunk.get(chunk_id)


class FakeAuditTrailRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(
        self,
        *,
        engagement_id: str,
        actor_id: str,
        action: str,
        subject_type: str,
        subject_id: str,
        before_state: dict[str, object] | None,
        after_state: dict[str, object] | None,
    ) -> None:
        self.events.append(
            {
                "engagement_id": engagement_id,
                "actor_id": actor_id,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "before_state": before_state,
                "after_state": after_state,
            }
        )


@pytest.fixture
def finding_repo() -> FakeFindingRepository:
    return FakeFindingRepository()


@pytest.fixture
def evidence_repo() -> FakeFindingEvidenceRepository:
    return FakeFindingEvidenceRepository()


@pytest.fixture
def report_repo() -> FakeReportRepository:
    return FakeReportRepository()


def make_service(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
    chunk_lookup: FakeChunkLookup | None = None,
    audit_recorder: FakeAuditTrailRecorder | None = None,
    report_file_storage: FakeReportFileStorage | None = None,
) -> ReportingService:
    return ReportingService(
        finding_repository=finding_repo,
        evidence_repository=evidence_repo,
        report_repository=report_repo,
        chunk_lookup=chunk_lookup or FakeChunkLookup(),
        audit_recorder=audit_recorder or FakeAuditTrailRecorder(),
        report_file_storage=report_file_storage or FakeReportFileStorage(),
    )


async def test_create_finding_starts_as_draft(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)

    finding = await service.create_finding(
        engagement_id="eng-1",
        title="Unapproved vendor payment",
        description="Payment issued without matching PO.",
        severity=FindingSeverity.HIGH,
        created_by="user-1",
    )

    assert finding.status == FindingStatus.DRAFT
    assert finding.engagement_id == "eng-1"
    assert finding.reviewed_by is None


async def test_attach_evidence_succeeds_when_chunk_belongs_to_the_findings_engagement(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    chunk_lookup = FakeChunkLookup({"chunk-1": "eng-1"})
    service = make_service(finding_repo, evidence_repo, report_repo, chunk_lookup)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    evidence = await service.attach_evidence(
        finding_id=finding.id, chunk_id="chunk-1", citation_text="see clause 4.2"
    )

    assert evidence.finding_id == finding.id
    assert evidence.engagement_id == "eng-1"
    assert len(evidence_repo.evidence) == 1


async def test_attach_evidence_raises_when_chunk_does_not_exist(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo, FakeChunkLookup({}))
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    with pytest.raises(ChunkNotFoundError):
        await service.attach_evidence(
            finding_id=finding.id, chunk_id="missing-chunk", citation_text="x"
        )


async def test_attach_evidence_raises_when_chunk_belongs_to_a_different_engagement(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    """The scenario ``ChunkLookup``'s docstring describes: a user who is a member of both
    engagements citing a chunk from engagement 2 on a finding that belongs to engagement 1. Row-
    Level Security alone would not catch this — both reads are individually authorized — so the
    application layer must reject it explicitly."""
    chunk_lookup = FakeChunkLookup({"chunk-from-eng-2": "eng-2"})
    service = make_service(finding_repo, evidence_repo, report_repo, chunk_lookup)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    with pytest.raises(ChunkEngagementMismatchError):
        await service.attach_evidence(
            finding_id=finding.id, chunk_id="chunk-from-eng-2", citation_text="x"
        )
    assert evidence_repo.evidence == []


async def test_confirm_finding_transitions_draft_to_confirmed(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    confirmed = await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-1")

    assert confirmed.status == FindingStatus.CONFIRMED
    assert confirmed.reviewed_by == "reviewer-1"
    assert confirmed.reviewed_at is not None


async def test_confirm_finding_records_an_audit_trail_event(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    """AC-04's append-only history exists to reconstruct exactly this: who dispositioned a
    finding, when, and what it looked like before — proving the wiring actually happens, not just
    that the port exists unused."""
    audit_recorder = FakeAuditTrailRecorder()
    service = make_service(finding_repo, evidence_repo, report_repo, audit_recorder=audit_recorder)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-1")

    assert len(audit_recorder.events) == 1
    event = audit_recorder.events[0]
    assert event["engagement_id"] == "eng-1"
    assert event["actor_id"] == "reviewer-1"
    assert event["action"] == "finding.confirmed"
    assert event["subject_id"] == finding.id
    assert event["before_state"] == {"status": "draft"}


async def test_confirm_finding_raises_when_finding_already_dispositioned(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )
    await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-1")

    with pytest.raises(InvalidFindingTransitionError):
        await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-2")


async def test_reject_finding_requires_a_non_empty_reason(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    with pytest.raises(MissingDispositionReasonError):
        await service.reject_finding(
            finding_id=finding.id, reviewed_by="reviewer-1", disposition_reason="   "
        )

    # Rejected only via a genuinely-empty (whitespace-only) reason — the finding must still be
    # sitting untouched in draft, not partially dispositioned.
    still_draft = await finding_repo.get(finding.id)
    assert still_draft is not None
    assert still_draft.status == FindingStatus.DRAFT


async def test_reject_finding_records_the_reason(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="t",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )

    rejected = await service.reject_finding(
        finding_id=finding.id,
        reviewed_by="reviewer-1",
        disposition_reason="Not supported by the underlying contract terms.",
    )

    assert rejected.status == FindingStatus.REJECTED
    assert rejected.disposition_reason == "Not supported by the underlying contract terms."


async def test_generate_report_only_includes_confirmed_findings(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    draft = await service.create_finding(
        engagement_id="eng-1",
        title="draft",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )
    to_confirm = await service.create_finding(
        engagement_id="eng-1",
        title="confirmed",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )
    to_reject = await service.create_finding(
        engagement_id="eng-1",
        title="rejected",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )
    confirmed = await service.confirm_finding(finding_id=to_confirm.id, reviewed_by="reviewer-1")
    await service.reject_finding(
        finding_id=to_reject.id, reviewed_by="reviewer-1", disposition_reason="not supported"
    )

    report = await service.generate_report(engagement_id="eng-1", generated_by="reviewer-1")

    assert report.finding_ids == (confirmed.id,)
    assert draft.id not in report.finding_ids
    assert to_reject.id not in report.finding_ids


async def test_generate_report_renders_evidence_cited_markdown(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    chunk_lookup = FakeChunkLookup({"chunk-1": "eng-1"})
    service = make_service(finding_repo, evidence_repo, report_repo, chunk_lookup)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="Duplicate vendor payment",
        description="Acme Supplies was paid twice for invoice #4471.",
        severity=FindingSeverity.HIGH,
        created_by="user-1",
        control_id="AP-14",
    )
    await service.attach_evidence(
        finding_id=finding.id, chunk_id="chunk-1", citation_text="Invoice #4471 paid twice."
    )
    await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-1")

    report = await service.generate_report(engagement_id="eng-1", generated_by="reviewer-1")

    assert "Duplicate vendor payment" in report.body_markdown
    assert "[HIGH]" in report.body_markdown
    assert "AP-14" in report.body_markdown
    assert "Invoice #4471 paid twice." in report.body_markdown
    assert "1 confirmed finding(s)" in report.body_markdown


async def test_get_or_export_report_pdf_generates_once_and_caches_the_uri(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    chunk_lookup = FakeChunkLookup({"chunk-1": "eng-1"})
    file_storage = FakeReportFileStorage()
    service = make_service(
        finding_repo, evidence_repo, report_repo, chunk_lookup, report_file_storage=file_storage
    )
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="Duplicate vendor payment",
        description="Acme Supplies was paid twice for invoice #4471.",
        severity=FindingSeverity.HIGH,
        created_by="user-1",
        control_id="AP-14",
    )
    await service.attach_evidence(
        finding_id=finding.id, chunk_id="chunk-1", citation_text="Invoice #4471 paid twice."
    )
    await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-1")
    report = await service.generate_report(engagement_id="eng-1", generated_by="reviewer-1")
    assert report.exported_uri is None  # never fabricated at generation time

    pdf_bytes = await service.get_or_export_report_pdf(report.id)

    assert pdf_bytes.startswith(b"%PDF")
    refetched = await service.get_report(report.id)
    assert refetched.exported_uri is not None

    # Second call serves the cached bytes rather than rendering (and re-storing) again.
    second_call_bytes = await service.get_or_export_report_pdf(report.id)
    assert second_call_bytes == pdf_bytes
    assert len(file_storage.files) == 1


async def test_generate_report_labels_findings_with_no_evidence_rather_than_fabricating_any(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    finding = await service.create_finding(
        engagement_id="eng-1",
        title="Unsupported observation",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="user-1",
    )
    await service.confirm_finding(finding_id=finding.id, reviewed_by="reviewer-1")

    report = await service.generate_report(engagement_id="eng-1", generated_by="reviewer-1")

    assert "**Evidence:** none attached." in report.body_markdown


async def test_generate_report_with_no_confirmed_findings_says_so_plainly(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)

    report = await service.generate_report(engagement_id="eng-1", generated_by="reviewer-1")

    assert "No findings had been confirmed" in report.body_markdown


async def test_generate_report_versions_increment_per_engagement(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)

    first = await service.generate_report(engagement_id="eng-1", generated_by="u")
    second = await service.generate_report(engagement_id="eng-1", generated_by="u")
    other_engagement_first = await service.generate_report(engagement_id="eng-2", generated_by="u")

    assert first.version == 1
    assert second.version == 2
    assert other_engagement_first.version == 1  # a separate engagement's own sequence


async def test_get_finding_raises_not_found_for_unknown_id(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)

    with pytest.raises(NotFoundError):
        await service.get_finding(str(uuid.uuid4()))


async def test_list_findings_scoped_to_the_requested_engagement_only(
    finding_repo: FakeFindingRepository,
    evidence_repo: FakeFindingEvidenceRepository,
    report_repo: FakeReportRepository,
) -> None:
    service = make_service(finding_repo, evidence_repo, report_repo)
    await service.create_finding(
        engagement_id="eng-1",
        title="a",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="u",
    )
    await service.create_finding(
        engagement_id="eng-2",
        title="b",
        description="d",
        severity=FindingSeverity.LOW,
        created_by="u",
    )

    result = await service.list_findings("eng-1")

    assert len(result) == 1
    assert result[0].engagement_id == "eng-1"
