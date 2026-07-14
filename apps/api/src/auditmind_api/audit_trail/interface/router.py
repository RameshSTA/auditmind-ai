"""HTTP routes for the audit_trail bounded context — read-only access to the append-only event
history other contexts' disposition actions write."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from auditmind_api.audit_trail.application.services import AuditTrailService
from auditmind_api.audit_trail.domain.entities import AuditEvent
from auditmind_api.audit_trail.interface.dependencies import get_audit_trail_service
from auditmind_api.identity.domain.entities import EngagementMembership
from auditmind_api.identity.interface.dependencies import require_engagement_member
from auditmind_api.shared.roles import CAN_READ_FINDINGS

router = APIRouter(tags=["audit_trail"])


def _audit_event_response(event: AuditEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "engagement_id": event.engagement_id,
        "actor_type": event.actor_type.value,
        "actor_id": event.actor_id,
        "action": event.action,
        "subject_type": event.subject_type,
        "subject_id": event.subject_id,
        "before_state": event.before_state,
        "after_state": event.after_state,
        "occurred_at": event.occurred_at.isoformat(),
    }


@router.get("/v1/engagements/{engagement_id}/audit-events")
async def list_audit_events(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    audit_trail_service: AuditTrailService = Depends(get_audit_trail_service),
) -> list[dict[str, object]]:
    """Reads the append-only audit history — there is no corresponding write endpoint anywhere
    in this API. Every event here was written by another context's own disposition action
    (finding confirm/reject, anomaly disposition), never by a caller of this route directly;
    no UPDATE or DELETE grant exists on this table for any application role, and this route
    doesn't even offer a POST to begin with."""
    events = await audit_trail_service.list_events(membership.engagement_id)
    return [_audit_event_response(e) for e in events]
