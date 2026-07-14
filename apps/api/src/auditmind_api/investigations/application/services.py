"""Application service orchestrating investigation case-building.

No SQL, no HTTP, no framework import here — only coordination of the ports, the same pattern every
prior context's application service establishes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from auditmind_api.investigations.application.exceptions import (
    InvalidSubjectTypeError,
    InvestigationAlreadyClosedError,
    MissingConclusionError,
    SubjectEngagementMismatchError,
    SubjectNotFoundError,
)
from auditmind_api.investigations.domain.entities import (
    Investigation,
    InvestigationItem,
    InvestigationStatus,
    InvestigationSubjectType,
)
from auditmind_api.investigations.domain.ports import (
    AuditTrailRecorder,
    InvestigationItemRepository,
    InvestigationRepository,
    InvestigationSubjectLookup,
)
from auditmind_api.shared.errors import NotFoundError


class InvestigationService:
    def __init__(
        self,
        investigation_repository: InvestigationRepository,
        item_repository: InvestigationItemRepository,
        subject_lookup: InvestigationSubjectLookup,
        audit_recorder: AuditTrailRecorder,
    ) -> None:
        self._investigations = investigation_repository
        self._items = item_repository
        self._subjects = subject_lookup
        self._audit = audit_recorder

    async def create_investigation(
        self,
        *,
        engagement_id: str,
        title: str,
        description: str,
        created_by: str,
    ) -> Investigation:
        investigation = Investigation(
            id=str(uuid.uuid4()),
            engagement_id=engagement_id,
            title=title,
            description=description,
            status=InvestigationStatus.OPEN,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        return await self._investigations.create(investigation)

    async def get_investigation(self, investigation_id: str) -> Investigation:
        investigation = await self._investigations.get(investigation_id)
        if investigation is None:
            raise NotFoundError(f"Investigation {investigation_id} not found.")
        return investigation

    async def list_investigations(self, engagement_id: str) -> list[Investigation]:
        return await self._investigations.list_for_engagement(engagement_id)

    async def add_item(
        self,
        *,
        investigation_id: str,
        subject_type: str,
        subject_id: str,
        added_by: str,
        note: str | None = None,
    ) -> InvestigationItem:
        investigation = await self.get_investigation(investigation_id)
        if investigation.status != InvestigationStatus.OPEN:
            raise InvestigationAlreadyClosedError(
                f"Investigation {investigation_id} is closed; items can no longer be added."
            )

        try:
            parsed_subject_type = InvestigationSubjectType(subject_type)
        except ValueError as exc:
            raise InvalidSubjectTypeError(
                f"'{subject_type}' is not a recognized investigation item subject type."
            ) from exc

        subject_engagement_id = await self._subjects.get_engagement_id(subject_type, subject_id)
        if subject_engagement_id is None:
            raise SubjectNotFoundError(f"{subject_type} {subject_id} not found.")
        if subject_engagement_id != investigation.engagement_id:
            raise SubjectEngagementMismatchError(
                f"{subject_type} {subject_id} belongs to a different engagement than "
                f"investigation {investigation_id}."
            )

        item = InvestigationItem(
            id=str(uuid.uuid4()),
            investigation_id=investigation_id,
            engagement_id=investigation.engagement_id,
            subject_type=parsed_subject_type,
            subject_id=subject_id,
            added_by=added_by,
            added_at=datetime.now(UTC),
            note=note,
        )
        created = await self._items.add(item)

        await self._audit.record(
            engagement_id=investigation.engagement_id,
            actor_id=added_by,
            action="investigation.item_added",
            subject_type="investigation",
            subject_id=investigation_id,
            before_state=None,
            after_state={"item_subject_type": subject_type, "item_subject_id": subject_id},
        )
        return created

    async def remove_item(self, *, investigation_id: str, item_id: str, removed_by: str) -> None:
        investigation = await self.get_investigation(investigation_id)
        if investigation.status != InvestigationStatus.OPEN:
            raise InvestigationAlreadyClosedError(
                f"Investigation {investigation_id} is closed; items can no longer be removed."
            )
        await self._items.remove(item_id)
        await self._audit.record(
            engagement_id=investigation.engagement_id,
            actor_id=removed_by,
            action="investigation.item_removed",
            subject_type="investigation",
            subject_id=investigation_id,
            before_state={"item_id": item_id},
            after_state=None,
        )

    async def list_items(self, investigation_id: str) -> list[InvestigationItem]:
        return await self._items.list_for_investigation(investigation_id)

    async def close_investigation(
        self, *, investigation_id: str, closed_by: str, conclusion: str
    ) -> Investigation:
        if not conclusion.strip():
            raise MissingConclusionError(
                "Closing an investigation requires a non-empty conclusion."
            )
        investigation = await self.get_investigation(investigation_id)
        if investigation.status != InvestigationStatus.OPEN:
            raise InvestigationAlreadyClosedError(
                f"Investigation {investigation_id} is already closed."
            )

        closed_at = datetime.now(UTC)
        await self._investigations.close(
            investigation_id=investigation_id,
            closed_by=closed_by,
            closed_at=closed_at,
            conclusion=conclusion,
        )
        updated = await self._investigations.get(investigation_id)
        assert updated is not None  # just written above, in the same transaction

        await self._audit.record(
            engagement_id=investigation.engagement_id,
            actor_id=closed_by,
            action="investigation.closed",
            subject_type="investigation",
            subject_id=investigation_id,
            before_state={"status": InvestigationStatus.OPEN.value},
            after_state={"status": InvestigationStatus.CLOSED.value, "conclusion": conclusion},
        )
        return updated
