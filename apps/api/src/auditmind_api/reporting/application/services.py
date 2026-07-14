"""Application service orchestrating findings, citations, and report generation (Phase 1 FR-7,
FR-8, Phase 4 §1).

No SQL, no HTTP, no framework import here — only coordination of the ports, the same pattern
``IdentityService`` and ``IngestionService`` already established.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from auditmind_api.reporting.application.exceptions import (
    ChunkEngagementMismatchError,
    ChunkNotFoundError,
    InvalidFindingTransitionError,
    MissingDispositionReasonError,
)
from auditmind_api.reporting.application.pdf_rendering import render_report_pdf
from auditmind_api.reporting.domain.entities import (
    Finding,
    FindingEvidence,
    FindingSeverity,
    FindingStatus,
    Report,
)
from auditmind_api.reporting.domain.ports import (
    AuditTrailRecorder,
    ChunkLookup,
    FindingEvidenceRepository,
    FindingRepository,
    ReportFileStorage,
    ReportRepository,
)
from auditmind_api.shared.errors import NotFoundError

_SEVERITY_ORDER: dict[FindingSeverity, int] = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.HIGH: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.LOW: 3,
}


def _render_report_markdown(
    *,
    engagement_id: str,
    version: int,
    findings: list[Finding],
    evidence_by_finding: dict[str, list[FindingEvidence]],
) -> str:
    """Renders a confirmed-findings snapshot as evidence-cited Markdown — every claim in the
    document traces back to either a cited passage (a real ``FindingEvidence`` row) or is
    explicitly labelled as having none, never presented as fact without one (the "citation-only
    answers" principle FR-8.1/AC-05 exist to enforce)."""
    ordered = sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.title))

    lines = [f"# Audit Report — Engagement {engagement_id} (v{version})", ""]

    if not ordered:
        lines.append(
            "No findings had been confirmed for this engagement at the time this report was "
            "generated."
        )
        return "\n".join(lines)

    counts: dict[FindingSeverity, int] = {severity: 0 for severity in FindingSeverity}
    for finding in ordered:
        counts[finding.severity] += 1
    summary = ", ".join(
        f"{counts[severity]} {severity.value}"
        for severity in _SEVERITY_ORDER
        if counts[severity] > 0
    )
    lines.append(f"**Executive summary:** {len(ordered)} confirmed finding(s) — {summary}.")
    lines.append("")

    for finding in ordered:
        lines.append(f"## [{finding.severity.value.upper()}] {finding.title}")
        lines.append("")
        control_note = f" · Control: {finding.control_id}" if finding.control_id else ""
        lines.append(f"*Status: {finding.status.value}{control_note}*")
        lines.append("")
        lines.append(finding.description)
        lines.append("")

        evidence = evidence_by_finding.get(finding.id, [])
        if evidence:
            citation_word = "citation" if len(evidence) == 1 else "citations"
            lines.append(f"**Evidence ({len(evidence)} {citation_word}):**")
            lines.append("")
            for item in evidence:
                quoted = item.citation_text.replace("\n", "\n> ")
                lines.append(f"> {quoted}")
                lines.append(">")
                lines.append(f"> — source chunk `{item.chunk_id[:8]}…`")
                lines.append("")
        else:
            lines.append("**Evidence:** none attached.")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class ReportingService:
    def __init__(
        self,
        finding_repository: FindingRepository,
        evidence_repository: FindingEvidenceRepository,
        report_repository: ReportRepository,
        chunk_lookup: ChunkLookup,
        audit_recorder: AuditTrailRecorder,
        report_file_storage: ReportFileStorage,
    ) -> None:
        self._findings = finding_repository
        self._evidence = evidence_repository
        self._reports = report_repository
        self._chunks = chunk_lookup
        self._audit = audit_recorder
        self._report_files = report_file_storage

    async def create_finding(
        self,
        *,
        engagement_id: str,
        title: str,
        description: str,
        severity: FindingSeverity,
        created_by: str,
        control_id: str | None = None,
    ) -> Finding:
        """Records a new finding, always ``draft`` (Phase 4 §1) — nothing is audit-ready until a
        human confirms it (FR-7.1)."""
        finding = Finding(
            id=str(uuid.uuid4()),
            engagement_id=engagement_id,
            title=title,
            description=description,
            severity=severity,
            status=FindingStatus.DRAFT,
            control_id=control_id,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        return await self._findings.create(finding)

    async def attach_evidence(
        self, *, finding_id: str, chunk_id: str, citation_text: str
    ) -> FindingEvidence:
        """Attaches one citation to a finding — the concrete building block of the citation graph
        FR-8.1/AC-05 require.

        Verifies the cited chunk actually belongs to the finding's own engagement before insert —
        see ``ChunkLookup``'s docstring for why this is needed even though Row-Level Security
        already governs the read: a user who is a member of two engagements could otherwise cite a
        chunk from one engagement on a finding that belongs to the other.
        """
        finding = await self._findings.get(finding_id)
        if finding is None:
            raise NotFoundError(f"Finding {finding_id} not found.")

        chunk_engagement_id = await self._chunks.get_engagement_id(chunk_id)
        if chunk_engagement_id is None:
            raise ChunkNotFoundError(f"Chunk {chunk_id} not found.")
        if chunk_engagement_id != finding.engagement_id:
            raise ChunkEngagementMismatchError(
                f"Chunk {chunk_id} belongs to a different engagement than finding {finding_id}."
            )

        evidence = FindingEvidence(
            id=str(uuid.uuid4()),
            finding_id=finding_id,
            engagement_id=finding.engagement_id,
            chunk_id=chunk_id,
            citation_text=citation_text,
            created_at=datetime.now(UTC),
        )
        return await self._evidence.create(evidence)

    async def confirm_finding(self, *, finding_id: str, reviewed_by: str) -> Finding:
        return await self._disposition_finding(
            finding_id=finding_id,
            reviewed_by=reviewed_by,
            new_status=FindingStatus.CONFIRMED,
            disposition_reason=None,
        )

    async def reject_finding(
        self, *, finding_id: str, reviewed_by: str, disposition_reason: str
    ) -> Finding:
        if not disposition_reason.strip():
            raise MissingDispositionReasonError(
                "Rejecting a finding requires a non-empty disposition reason."
            )
        return await self._disposition_finding(
            finding_id=finding_id,
            reviewed_by=reviewed_by,
            new_status=FindingStatus.REJECTED,
            disposition_reason=disposition_reason,
        )

    async def _disposition_finding(
        self,
        *,
        finding_id: str,
        reviewed_by: str,
        new_status: FindingStatus,
        disposition_reason: str | None,
    ) -> Finding:
        finding = await self._findings.get(finding_id)
        if finding is None:
            raise NotFoundError(f"Finding {finding_id} not found.")
        if finding.status != FindingStatus.DRAFT:
            raise InvalidFindingTransitionError(
                f"Finding {finding_id} is already {finding.status.value}, not awaiting disposition."
            )

        reviewed_at = datetime.now(UTC)
        await self._findings.update_disposition(
            finding_id=finding_id,
            status=new_status,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            disposition_reason=disposition_reason,
        )
        updated = await self._findings.get(finding_id)
        assert updated is not None  # just written above, in the same transaction

        await self._audit.record(
            engagement_id=finding.engagement_id,
            actor_id=reviewed_by,
            action=f"finding.{new_status.value}",
            subject_type="finding",
            subject_id=finding_id,
            before_state={"status": finding.status.value},
            after_state={"status": new_status.value, "disposition_reason": disposition_reason},
        )
        return updated

    async def list_findings(self, engagement_id: str) -> list[Finding]:
        return await self._findings.list_for_engagement(engagement_id)

    async def get_finding(self, finding_id: str) -> Finding:
        finding = await self._findings.get(finding_id)
        if finding is None:
            raise NotFoundError(f"Finding {finding_id} not found.")
        return finding

    async def list_finding_evidence(self, finding_id: str) -> list[FindingEvidence]:
        return await self._evidence.list_for_finding(finding_id)

    async def generate_report(self, *, engagement_id: str, generated_by: str) -> Report:
        """Compiles every currently ``confirmed`` finding for an engagement into a new, versioned
        report snapshot (FR-8.1), rendered as an evidence-cited Markdown document.

        Deliberately reads from ``list_confirmed_for_engagement`` rather than accepting a
        caller-supplied list of finding ids — the acceptance criterion this implements
        ("no report content sourced from an unconfirmed AI draft") is enforced structurally, not
        by trusting every call site to filter correctly.

        The rendered ``body_markdown`` is snapshotted once, here, alongside ``finding_ids`` — not
        recomputed on every read — so a report's content can never drift if evidence is attached
        to one of its findings after the fact.
        """
        confirmed = await self._findings.list_confirmed_for_engagement(engagement_id)
        version = await self._reports.next_version_for_engagement(engagement_id)
        evidence_by_finding = {
            finding.id: await self._evidence.list_for_finding(finding.id) for finding in confirmed
        }
        report = Report(
            id=str(uuid.uuid4()),
            engagement_id=engagement_id,
            version=version,
            generated_by=generated_by,
            generated_at=datetime.now(UTC),
            finding_ids=tuple(f.id for f in confirmed),
            body_markdown=_render_report_markdown(
                engagement_id=engagement_id,
                version=version,
                findings=confirmed,
                evidence_by_finding=evidence_by_finding,
            ),
        )
        return await self._reports.create(report)

    async def list_reports(self, engagement_id: str) -> list[Report]:
        return await self._reports.list_for_engagement(engagement_id)

    async def get_report(self, report_id: str) -> Report:
        report = await self._reports.get(report_id)
        if report is None:
            raise NotFoundError(f"Report {report_id} not found.")
        return report

    async def get_or_export_report_pdf(self, report_id: str) -> bytes:
        """Lazily generates a report's PDF on first request, then serves the same stored bytes on
        every later request — one round trip for the caller instead of a separate "export" action
        followed by a separate "download" action, at the cost of the first request being slower
        than the rest (real generation work, not a cache miss masking a bug).

        Rendered from the same ``findings``/``evidence_by_finding`` a Markdown report reads from
        (re-fetched here, not re-parsed out of ``body_markdown``) — one source of truth for a
        report's actual content regardless of which format a reader asks for.
        """
        report = await self.get_report(report_id)
        if report.exported_uri is not None:
            return await self._report_files.get(report.exported_uri)

        all_findings = await self._findings.list_for_engagement(report.engagement_id)
        findings = [f for f in all_findings if f.id in report.finding_ids]
        evidence_by_finding = {
            finding.id: await self._evidence.list_for_finding(finding.id) for finding in findings
        }
        pdf_bytes = render_report_pdf(
            version=report.version,
            generated_by=report.generated_by,
            generated_at=report.generated_at,
            findings=findings,
            evidence_by_finding=evidence_by_finding,
        )
        storage_uri = await self._report_files.put(
            engagement_id=report.engagement_id, report_id=report.id, content=pdf_bytes
        )
        await self._reports.set_exported_uri(report_id=report.id, exported_uri=storage_uri)
        return pdf_bytes
