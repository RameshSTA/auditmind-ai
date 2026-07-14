"""Unit tests for AuditTrailService (application layer) — against an in-memory fake."""

from __future__ import annotations

from auditmind_api.audit_trail.application.services import AuditTrailService
from auditmind_api.audit_trail.domain.entities import ActorType, AuditEvent


class FakeAuditEventRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def create(self, event: AuditEvent) -> AuditEvent:
        self.events.append(event)
        return event

    async def list_for_engagement(self, engagement_id: str) -> list[AuditEvent]:
        return [e for e in self.events if e.engagement_id == engagement_id]


async def test_record_event_persists_actor_action_and_state() -> None:
    repo = FakeAuditEventRepository()
    service = AuditTrailService(event_repository=repo)

    event = await service.record_event(
        engagement_id="eng-1",
        actor_type=ActorType.HUMAN,
        actor_id="user-1",
        action="finding.confirmed",
        subject_type="finding",
        subject_id="finding-1",
        before_state={"status": "draft"},
        after_state={"status": "confirmed"},
    )

    assert event.engagement_id == "eng-1"
    assert event.action == "finding.confirmed"
    assert event.before_state == {"status": "draft"}
    assert len(repo.events) == 1


async def test_list_events_scoped_to_the_requested_engagement_only() -> None:
    repo = FakeAuditEventRepository()
    service = AuditTrailService(event_repository=repo)
    await service.record_event(
        engagement_id="eng-1",
        actor_type=ActorType.HUMAN,
        actor_id="user-1",
        action="finding.confirmed",
        subject_type="finding",
        subject_id="finding-1",
        before_state=None,
        after_state=None,
    )
    await service.record_event(
        engagement_id="eng-2",
        actor_type=ActorType.HUMAN,
        actor_id="user-2",
        action="finding.confirmed",
        subject_type="finding",
        subject_id="finding-2",
        before_state=None,
        after_state=None,
    )

    result = await service.list_events("eng-1")

    assert len(result) == 1
    assert result[0].engagement_id == "eng-1"
