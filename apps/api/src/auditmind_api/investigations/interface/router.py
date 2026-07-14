"""HTTP routes for the investigations bounded context — investigation cases and their items."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from auditmind_api.identity.domain.entities import EngagementMembership, User
from auditmind_api.identity.interface.dependencies import (
    get_current_db_user,
    require_engagement_member,
)
from auditmind_api.investigations.application.services import InvestigationService
from auditmind_api.investigations.domain.entities import Investigation, InvestigationItem
from auditmind_api.investigations.interface.dependencies import get_investigation_service
from auditmind_api.investigations.interface.schemas import (
    AddInvestigationItemRequest,
    CloseInvestigationRequest,
    CreateInvestigationRequest,
)
from auditmind_api.shared.roles import (
    CAN_AUTHOR_FINDINGS,
    CAN_DISPOSITION_FINDINGS,
    CAN_READ_FINDINGS,
)

router = APIRouter(tags=["investigations"])


def _investigation_response(investigation: Investigation) -> dict[str, object]:
    return {
        "id": investigation.id,
        "engagement_id": investigation.engagement_id,
        "title": investigation.title,
        "description": investigation.description,
        "status": investigation.status.value,
        "created_by": investigation.created_by,
        "created_at": investigation.created_at.isoformat(),
        "closed_by": investigation.closed_by,
        "closed_at": investigation.closed_at.isoformat() if investigation.closed_at else None,
        "conclusion": investigation.conclusion,
    }


def _investigation_item_response(item: InvestigationItem) -> dict[str, object]:
    return {
        "id": item.id,
        "investigation_id": item.investigation_id,
        "subject_type": item.subject_type.value,
        "subject_id": item.subject_id,
        "added_by": item.added_by,
        "added_at": item.added_at.isoformat(),
        "note": item.note,
    }


@router.post("/v1/engagements/{engagement_id}/investigations", status_code=201)
async def create_investigation(
    body: CreateInvestigationRequest,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    db_user: User = Depends(get_current_db_user),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> dict[str, object]:
    """Opens a new investigation case — a working set an investigator builds up from findings,
    anomalies, and transactions already recorded elsewhere. Always starts ``open``; every
    item added and the eventual close both go through their own endpoints below."""
    investigation = await investigation_service.create_investigation(
        engagement_id=membership.engagement_id,
        title=body.title,
        description=body.description,
        created_by=db_user.id,
    )
    return _investigation_response(investigation)


@router.get("/v1/engagements/{engagement_id}/investigations")
async def list_investigations(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> list[dict[str, object]]:
    investigations = await investigation_service.list_investigations(membership.engagement_id)
    return [_investigation_response(i) for i in investigations]


@router.get("/v1/engagements/{engagement_id}/investigations/{investigation_id}")
async def get_investigation(
    investigation_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> dict[str, object]:
    """As with ``get_finding``, no explicit check here confirms ``investigation_id`` belongs to
    the ``engagement_id`` in the path beyond the membership check itself — Row-Level Security
    on ``investigations.investigations`` (the same subquery-based policy every prior context's
    tables use) returns nothing if it doesn't."""
    investigation = await investigation_service.get_investigation(investigation_id)
    return _investigation_response(investigation)


@router.post(
    "/v1/engagements/{engagement_id}/investigations/{investigation_id}/items",
    status_code=201,
)
async def add_investigation_item(
    investigation_id: str,
    body: AddInvestigationItemRequest,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    db_user: User = Depends(get_current_db_user),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> dict[str, object]:
    """Pulls one finding, anomaly, or transaction into the investigation's working set — the
    actual case-building action. Rejects a subject from a different engagement than this
    investigation even if the caller can otherwise read it (see
    ``InvestigationSubjectLookup``'s docstring for why)."""
    item = await investigation_service.add_item(
        investigation_id=investigation_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        added_by=db_user.id,
        note=body.note,
    )
    return _investigation_item_response(item)


@router.get("/v1/engagements/{engagement_id}/investigations/{investigation_id}/items")
async def list_investigation_items(
    investigation_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> list[dict[str, object]]:
    items = await investigation_service.list_items(investigation_id)
    return [_investigation_item_response(i) for i in items]


@router.delete(
    "/v1/engagements/{engagement_id}/investigations/{investigation_id}/items/{item_id}",
    status_code=204,
    response_model=None,
)
async def remove_investigation_item(
    investigation_id: str,
    item_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    db_user: User = Depends(get_current_db_user),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> None:
    await investigation_service.remove_item(
        investigation_id=investigation_id, item_id=item_id, removed_by=db_user.id
    )


@router.post("/v1/engagements/{engagement_id}/investigations/{investigation_id}/close")
async def close_investigation(
    investigation_id: str,
    body: CloseInvestigationRequest,
    membership: EngagementMembership = Depends(
        require_engagement_member(*CAN_DISPOSITION_FINDINGS)
    ),
    db_user: User = Depends(get_current_db_user),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> dict[str, object]:
    """Closes an investigation with a documented conclusion — restricted to Auditor/Fraud
    Analyst, the same two roles ``confirm_finding``/``reject_finding`` restrict to, since
    closing a case is the same professional-judgment sign-off."""
    investigation = await investigation_service.close_investigation(
        investigation_id=investigation_id,
        closed_by=db_user.id,
        conclusion=body.conclusion,
    )
    return _investigation_response(investigation)
