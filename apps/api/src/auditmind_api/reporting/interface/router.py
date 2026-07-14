"""HTTP routes for the reporting bounded context — findings, evidence citations, and reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from auditmind_api.identity.domain.entities import EngagementMembership, User
from auditmind_api.identity.interface.dependencies import (
    get_current_db_user,
    require_engagement_member,
)
from auditmind_api.reporting.application.services import ReportingService
from auditmind_api.reporting.domain.entities import Finding, Report
from auditmind_api.reporting.interface.dependencies import get_reporting_service
from auditmind_api.reporting.interface.schemas import (
    AttachEvidenceRequest,
    CreateFindingRequest,
    RejectFindingRequest,
)
from auditmind_api.shared.metrics import findings_created_total
from auditmind_api.shared.roles import (
    CAN_AUTHOR_FINDINGS,
    CAN_DISPOSITION_FINDINGS,
    CAN_READ_FINDINGS,
)

router = APIRouter(tags=["reporting"])


def _finding_response(finding: Finding) -> dict[str, object]:
    return {
        "id": finding.id,
        "engagement_id": finding.engagement_id,
        "control_id": finding.control_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity.value,
        "status": finding.status.value,
        "created_by": finding.created_by,
        "disposition_reason": finding.disposition_reason,
        "reviewed_by": finding.reviewed_by,
    }


def _report_response(report: Report) -> dict[str, object]:
    return {
        "id": report.id,
        "engagement_id": report.engagement_id,
        "version": report.version,
        "generated_by": report.generated_by,
        "generated_at": report.generated_at.isoformat(),
        "finding_ids": list(report.finding_ids),
        "body_markdown": report.body_markdown,
        "exported_uri": report.exported_uri,
    }


@router.post("/v1/engagements/{engagement_id}/findings", status_code=201)
async def create_finding(
    body: CreateFindingRequest,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    db_user: User = Depends(get_current_db_user),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    """Records a new draft finding (Phase 1 FR-7.1, Phase 4 §1).

    Every finding created through this endpoint is manually authored — there is no agent
    orchestrator yet to produce ``source_run_id``-linked findings (see the increment doc's
    deferred section), so a human recording an observation directly is this increment's only
    path to a finding at all.
    """
    finding = await reporting_service.create_finding(
        engagement_id=membership.engagement_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        control_id=body.control_id,
        created_by=db_user.id,
    )
    findings_created_total.add(1, {"severity": finding.severity.value})
    return _finding_response(finding)


@router.get("/v1/engagements/{engagement_id}/findings")
async def list_findings(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> list[dict[str, object]]:
    findings = await reporting_service.list_findings(membership.engagement_id)
    return [_finding_response(f) for f in findings]


@router.get("/v1/engagements/{engagement_id}/findings/{finding_id}")
async def get_finding(
    finding_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    """Returns one finding.

    As with ``list_document_chunks`` in the ingestion context, no explicit check here confirms
    ``finding_id`` belongs to the ``engagement_id`` in the path beyond the membership check
    itself — Row-Level Security on ``reporting.findings`` (the same subquery-based policy as
    ``ingestion.documents``) returns nothing if it doesn't.
    """
    finding = await reporting_service.get_finding(finding_id)
    return _finding_response(finding)


@router.post(
    "/v1/engagements/{engagement_id}/findings/{finding_id}/evidence",
    status_code=201,
)
async def attach_evidence(
    finding_id: str,
    body: AttachEvidenceRequest,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    """Attaches one citation to a finding — the citation graph FR-8.1/AC-05 require."""
    evidence = await reporting_service.attach_evidence(
        finding_id=finding_id, chunk_id=body.chunk_id, citation_text=body.citation_text
    )
    return {
        "id": evidence.id,
        "finding_id": evidence.finding_id,
        "chunk_id": evidence.chunk_id,
        "citation_text": evidence.citation_text,
    }


@router.get("/v1/engagements/{engagement_id}/findings/{finding_id}/evidence")
async def list_finding_evidence(
    finding_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> list[dict[str, object]]:
    evidence = await reporting_service.list_finding_evidence(finding_id)
    return [
        {"id": e.id, "chunk_id": e.chunk_id, "citation_text": e.citation_text} for e in evidence
    ]


@router.post("/v1/engagements/{engagement_id}/findings/{finding_id}/confirm")
async def confirm_finding(
    finding_id: str,
    membership: EngagementMembership = Depends(
        require_engagement_member(*CAN_DISPOSITION_FINDINGS)
    ),
    db_user: User = Depends(get_current_db_user),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    """The mandatory human sign-off gate itself (FR-7.1) — restricted to Auditor/Fraud Analyst
    per Phase 11 §2's RBAC matrix, the only two roles permitted to confirm or reject a finding.
    """
    finding = await reporting_service.confirm_finding(finding_id=finding_id, reviewed_by=db_user.id)
    return _finding_response(finding)


@router.post("/v1/engagements/{engagement_id}/findings/{finding_id}/reject")
async def reject_finding(
    finding_id: str,
    body: RejectFindingRequest,
    membership: EngagementMembership = Depends(
        require_engagement_member(*CAN_DISPOSITION_FINDINGS)
    ),
    db_user: User = Depends(get_current_db_user),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    """Rejects a finding with a documented reason (US-06: "reject ... with a documented
    reason, so that professional judgment stays in control")."""
    finding = await reporting_service.reject_finding(
        finding_id=finding_id,
        reviewed_by=db_user.id,
        disposition_reason=body.disposition_reason,
    )
    return _finding_response(finding)


@router.post("/v1/engagements/{engagement_id}/reports", status_code=201)
async def generate_report(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    db_user: User = Depends(get_current_db_user),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    """Compiles every currently-confirmed finding into a new versioned report (FR-8.1).

    ``exported_uri`` is null in this response regardless — a PDF is rendered lazily, on the first
    call to the ``/pdf`` route below, not eagerly at generation time (most reports are read on
    screen and never exported at all).
    """
    report = await reporting_service.generate_report(
        engagement_id=membership.engagement_id, generated_by=db_user.id
    )
    return _report_response(report)


@router.get("/v1/engagements/{engagement_id}/reports")
async def list_reports(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> list[dict[str, object]]:
    reports = await reporting_service.list_reports(membership.engagement_id)
    return [_report_response(r) for r in reports]


@router.get("/v1/engagements/{engagement_id}/reports/{report_id}")
async def get_report(
    report_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> dict[str, object]:
    report = await reporting_service.get_report(report_id)
    return _report_response(report)


@router.get("/v1/engagements/{engagement_id}/reports/{report_id}/pdf")
async def download_report_pdf(
    report_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    reporting_service: ReportingService = Depends(get_reporting_service),
) -> Response:
    """Real PDF export (FR-8.1's "exportable to standard document formats"), rendered from the
    same confirmed findings and cited evidence the on-screen/Markdown report reads from — not a
    second, independently-maintained copy of the report's content.

    Deliberately GET, not POST-then-GET: the first call generates and stores the PDF (lazily —
    see ``get_or_export_report_pdf``'s docstring), every later call serves the same stored bytes,
    and either way the caller gets a file back in one request instead of an export step followed
    by a separate download step.
    """
    pdf_bytes = await reporting_service.get_or_export_report_pdf(report_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit-report-{report_id[:8]}.pdf"'},
    )
