"""Postgres-backed implementations of the reporting repository ports (Phase 3 §1)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.reporting.domain.entities import (
    Finding,
    FindingEvidence,
    FindingSeverity,
    FindingStatus,
    Report,
)
from auditmind_api.reporting.infrastructure.models import (
    FindingEvidenceModel,
    FindingModel,
    ReportFindingModel,
    ReportModel,
)


def _to_finding_entity(model: FindingModel) -> Finding:
    return Finding(
        id=str(model.id),
        engagement_id=str(model.engagement_id),
        title=model.title,
        description=model.description,
        severity=FindingSeverity(model.severity),
        status=FindingStatus(model.status),
        control_id=model.control_id,
        created_by=str(model.created_by),
        created_at=model.created_at,
        disposition_reason=model.disposition_reason,
        reviewed_by=str(model.reviewed_by) if model.reviewed_by else None,
        reviewed_at=model.reviewed_at,
    )


def _to_evidence_entity(model: FindingEvidenceModel) -> FindingEvidence:
    return FindingEvidence(
        id=str(model.id),
        finding_id=str(model.finding_id),
        engagement_id=str(model.engagement_id),
        chunk_id=str(model.chunk_id),
        citation_text=model.citation_text,
        created_at=model.created_at,
    )


class PostgresFindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, finding: Finding) -> Finding:
        model = FindingModel(
            id=finding.id,
            engagement_id=finding.engagement_id,
            control_id=finding.control_id,
            title=finding.title,
            description=finding.description,
            severity=finding.severity.value,
            status=finding.status.value,
            created_by=finding.created_by,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_finding_entity(model)

    async def get(self, finding_id: str) -> Finding | None:
        result = await self._session.execute(
            select(FindingModel).where(FindingModel.id == finding_id)
        )
        model = result.scalars().first()
        return _to_finding_entity(model) if model else None

    async def list_for_engagement(self, engagement_id: str) -> list[Finding]:
        result = await self._session.execute(
            select(FindingModel).where(FindingModel.engagement_id == engagement_id)
        )
        return [_to_finding_entity(m) for m in result.scalars().all()]

    async def list_confirmed_for_engagement(self, engagement_id: str) -> list[Finding]:
        result = await self._session.execute(
            select(FindingModel).where(
                FindingModel.engagement_id == engagement_id,
                FindingModel.status == FindingStatus.CONFIRMED.value,
            )
        )
        return [_to_finding_entity(m) for m in result.scalars().all()]

    async def update_disposition(
        self,
        *,
        finding_id: str,
        status: FindingStatus,
        reviewed_by: str,
        reviewed_at: datetime,
        disposition_reason: str | None,
    ) -> None:
        result = await self._session.execute(
            select(FindingModel).where(FindingModel.id == finding_id)
        )
        model = result.scalar_one()
        model.status = status.value
        model.reviewed_by = uuid.UUID(reviewed_by)
        model.reviewed_at = reviewed_at
        model.disposition_reason = disposition_reason
        await self._session.flush()


class PostgresFindingEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, evidence: FindingEvidence) -> FindingEvidence:
        model = FindingEvidenceModel(
            id=evidence.id,
            finding_id=evidence.finding_id,
            engagement_id=evidence.engagement_id,
            chunk_id=evidence.chunk_id,
            citation_text=evidence.citation_text,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _to_evidence_entity(model)

    async def list_for_finding(self, finding_id: str) -> list[FindingEvidence]:
        result = await self._session.execute(
            select(FindingEvidenceModel).where(FindingEvidenceModel.finding_id == finding_id)
        )
        return [_to_evidence_entity(m) for m in result.scalars().all()]


class PostgresReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, report: Report) -> Report:
        model = ReportModel(
            id=report.id,
            engagement_id=report.engagement_id,
            version=report.version,
            generated_by=report.generated_by,
            body_markdown=report.body_markdown,
            exported_uri=report.exported_uri,
        )
        self._session.add(model)
        await self._session.flush()

        self._session.add_all(
            ReportFindingModel(report_id=report.id, finding_id=finding_id)
            for finding_id in report.finding_ids
        )
        await self._session.flush()
        await self._session.refresh(model)
        return Report(
            id=str(model.id),
            engagement_id=str(model.engagement_id),
            version=model.version,
            generated_by=str(model.generated_by),
            generated_at=model.generated_at,
            finding_ids=report.finding_ids,
            body_markdown=model.body_markdown,
            exported_uri=model.exported_uri,
        )

    async def get(self, report_id: str) -> Report | None:
        result = await self._session.execute(select(ReportModel).where(ReportModel.id == report_id))
        model = result.scalars().first()
        if model is None:
            return None
        finding_ids = await self._finding_ids_for_reports([str(model.id)])
        return self._to_report_entity(model, finding_ids.get(str(model.id), []))

    async def list_for_engagement(self, engagement_id: str) -> list[Report]:
        result = await self._session.execute(
            select(ReportModel).where(ReportModel.engagement_id == engagement_id)
        )
        models = result.scalars().all()
        finding_ids = await self._finding_ids_for_reports([str(m.id) for m in models])
        return [self._to_report_entity(m, finding_ids.get(str(m.id), [])) for m in models]

    async def next_version_for_engagement(self, engagement_id: str) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(ReportModel.version), 0)).where(
                ReportModel.engagement_id == engagement_id
            )
        )
        return result.scalar_one() + 1

    async def set_exported_uri(self, *, report_id: str, exported_uri: str) -> None:
        await self._session.execute(
            update(ReportModel).where(ReportModel.id == report_id).values(exported_uri=exported_uri)
        )
        await self._session.flush()

    async def _finding_ids_for_reports(self, report_ids: list[str]) -> dict[str, list[str]]:
        if not report_ids:
            return {}
        result = await self._session.execute(
            select(ReportFindingModel).where(ReportFindingModel.report_id.in_(report_ids))
        )
        by_report: dict[str, list[str]] = defaultdict(list)
        for row in result.scalars().all():
            by_report[str(row.report_id)].append(str(row.finding_id))
        return dict(by_report)

    @staticmethod
    def _to_report_entity(model: ReportModel, finding_ids: list[str]) -> Report:
        return Report(
            id=str(model.id),
            engagement_id=str(model.engagement_id),
            version=model.version,
            generated_by=str(model.generated_by),
            generated_at=model.generated_at,
            finding_ids=tuple(finding_ids),
            body_markdown=model.body_markdown,
            exported_uri=model.exported_uri,
        )
