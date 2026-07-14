"""Unit tests for InvestigationService (application layer) — against in-memory fakes,
no database involved."""

from __future__ import annotations

import uuid

import pytest

from auditmind_api.investigations.application.exceptions import (
    InvalidSubjectTypeError,
    InvestigationAlreadyClosedError,
    MissingConclusionError,
    SubjectEngagementMismatchError,
    SubjectNotFoundError,
)
from auditmind_api.investigations.application.services import InvestigationService
from auditmind_api.investigations.domain.entities import (
    Investigation,
    InvestigationItem,
    InvestigationStatus,
)
from auditmind_api.shared.errors import NotFoundError


class FakeInvestigationRepository:
    def __init__(self) -> None:
        self.investigations: dict[str, Investigation] = {}

    async def create(self, investigation: Investigation) -> Investigation:
        self.investigations[investigation.id] = investigation
        return investigation

    async def get(self, investigation_id: str) -> Investigation | None:
        return self.investigations.get(investigation_id)

    async def list_for_engagement(self, engagement_id: str) -> list[Investigation]:
        return [i for i in self.investigations.values() if i.engagement_id == engagement_id]

    async def close(
        self, *, investigation_id: str, closed_by: str, closed_at, conclusion: str
    ) -> None:
        existing = self.investigations[investigation_id]
        self.investigations[investigation_id] = Investigation(
            id=existing.id,
            engagement_id=existing.engagement_id,
            title=existing.title,
            description=existing.description,
            status=InvestigationStatus.CLOSED,
            created_by=existing.created_by,
            created_at=existing.created_at,
            closed_by=closed_by,
            closed_at=closed_at,
            conclusion=conclusion,
        )


class FakeInvestigationItemRepository:
    def __init__(self) -> None:
        self.items: dict[str, InvestigationItem] = {}

    async def add(self, item: InvestigationItem) -> InvestigationItem:
        self.items[item.id] = item
        return item

    async def remove(self, item_id: str) -> None:
        del self.items[item_id]

    async def list_for_investigation(self, investigation_id: str) -> list[InvestigationItem]:
        return [i for i in self.items.values() if i.investigation_id == investigation_id]


class FakeSubjectLookup:
    def __init__(self, engagement_by_subject: dict[str, str] | None = None) -> None:
        self.engagement_by_subject = engagement_by_subject or {}

    async def get_engagement_id(self, subject_type: str, subject_id: str) -> str | None:
        return self.engagement_by_subject.get(subject_id)


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
            }
        )


@pytest.fixture
def investigation_repo() -> FakeInvestigationRepository:
    return FakeInvestigationRepository()


@pytest.fixture
def item_repo() -> FakeInvestigationItemRepository:
    return FakeInvestigationItemRepository()


def make_service(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
    subject_lookup: FakeSubjectLookup | None = None,
    audit_recorder: FakeAuditTrailRecorder | None = None,
) -> InvestigationService:
    return InvestigationService(
        investigation_repository=investigation_repo,
        item_repository=item_repo,
        subject_lookup=subject_lookup or FakeSubjectLookup(),
        audit_recorder=audit_recorder or FakeAuditTrailRecorder(),
    )


async def test_create_investigation_starts_open(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo)

    investigation = await service.create_investigation(
        engagement_id="eng-1",
        title="Vendor XYZ round-dollar pattern",
        description="Multiple round-dollar payments flagged.",
        created_by="user-1",
    )

    assert investigation.status == InvestigationStatus.OPEN
    assert investigation.engagement_id == "eng-1"
    assert investigation.closed_at is None


async def test_add_item_succeeds_when_subject_belongs_to_the_investigations_engagement(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    subject_lookup = FakeSubjectLookup({"txn-1": "eng-1"})
    service = make_service(investigation_repo, item_repo, subject_lookup)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )

    item = await service.add_item(
        investigation_id=investigation.id,
        subject_type="transaction",
        subject_id="txn-1",
        added_by="user-1",
        note="Looks anomalous",
    )

    assert item.investigation_id == investigation.id
    assert item.subject_id == "txn-1"
    assert item.note == "Looks anomalous"


async def test_add_item_rejects_subject_from_a_different_engagement(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    """The cross-engagement check InvestigationSubjectLookup exists for: a subject that's real
    but belongs to a different engagement than the investigation must not be citable, even though
    RLS alone would let the caller read it if they're a member of both engagements."""
    subject_lookup = FakeSubjectLookup({"txn-1": "eng-2"})
    service = make_service(investigation_repo, item_repo, subject_lookup)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )

    with pytest.raises(SubjectEngagementMismatchError):
        await service.add_item(
            investigation_id=investigation.id,
            subject_type="transaction",
            subject_id="txn-1",
            added_by="user-1",
        )


async def test_add_item_rejects_a_subject_that_does_not_exist(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo, FakeSubjectLookup())
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )

    with pytest.raises(SubjectNotFoundError):
        await service.add_item(
            investigation_id=investigation.id,
            subject_type="transaction",
            subject_id=str(uuid.uuid4()),
            added_by="user-1",
        )


async def test_add_item_rejects_an_unrecognized_subject_type(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )

    with pytest.raises(InvalidSubjectTypeError):
        await service.add_item(
            investigation_id=investigation.id,
            subject_type="vendor",
            subject_id="v-1",
            added_by="user-1",
        )


async def test_add_item_rejects_once_investigation_is_closed(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    subject_lookup = FakeSubjectLookup({"txn-1": "eng-1"})
    service = make_service(investigation_repo, item_repo, subject_lookup)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )
    await service.close_investigation(
        investigation_id=investigation.id, closed_by="user-1", conclusion="Resolved."
    )

    with pytest.raises(InvestigationAlreadyClosedError):
        await service.add_item(
            investigation_id=investigation.id,
            subject_type="transaction",
            subject_id="txn-1",
            added_by="user-1",
        )


async def test_close_investigation_requires_a_non_empty_conclusion(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )

    with pytest.raises(MissingConclusionError):
        await service.close_investigation(
            investigation_id=investigation.id, closed_by="user-1", conclusion="   "
        )


async def test_close_investigation_rejects_an_already_closed_investigation(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )
    await service.close_investigation(
        investigation_id=investigation.id, closed_by="user-1", conclusion="Resolved."
    )

    with pytest.raises(InvestigationAlreadyClosedError):
        await service.close_investigation(
            investigation_id=investigation.id, closed_by="user-1", conclusion="Again."
        )


async def test_close_investigation_records_conclusion_and_closer(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )

    closed = await service.close_investigation(
        investigation_id=investigation.id,
        closed_by="user-2",
        conclusion="False positive after reviewing the underlying invoices.",
    )

    assert closed.status == InvestigationStatus.CLOSED
    assert closed.closed_by == "user-2"
    assert closed.conclusion == "False positive after reviewing the underlying invoices."


async def test_get_investigation_raises_not_found_for_unknown_id(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    service = make_service(investigation_repo, item_repo)

    with pytest.raises(NotFoundError):
        await service.get_investigation(str(uuid.uuid4()))


async def test_remove_item_rejects_once_investigation_is_closed(
    investigation_repo: FakeInvestigationRepository,
    item_repo: FakeInvestigationItemRepository,
) -> None:
    subject_lookup = FakeSubjectLookup({"txn-1": "eng-1"})
    service = make_service(investigation_repo, item_repo, subject_lookup)
    investigation = await service.create_investigation(
        engagement_id="eng-1", title="Case", description="d", created_by="user-1"
    )
    item = await service.add_item(
        investigation_id=investigation.id,
        subject_type="transaction",
        subject_id="txn-1",
        added_by="user-1",
    )
    await service.close_investigation(
        investigation_id=investigation.id, closed_by="user-1", conclusion="Resolved."
    )

    with pytest.raises(InvestigationAlreadyClosedError):
        await service.remove_item(
            investigation_id=investigation.id, item_id=item.id, removed_by="user-1"
        )
