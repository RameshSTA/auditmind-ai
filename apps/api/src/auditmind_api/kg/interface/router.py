"""HTTP routes for the knowledge_graph bounded context — vendor resolution and vendor-360 reads."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from auditmind_api.identity.domain.entities import EngagementMembership
from auditmind_api.identity.interface.dependencies import require_engagement_member
from auditmind_api.kg.application.services import KnowledgeGraphService
from auditmind_api.kg.domain.entities import VendorEntity, VendorNetwork
from auditmind_api.kg.interface.dependencies import get_knowledge_graph_service
from auditmind_api.shared.roles import CAN_AUTHOR_FINDINGS, CAN_READ_FINDINGS

router = APIRouter(tags=["knowledge_graph"])


def _vendor_response(vendor: VendorEntity) -> dict[str, object]:
    return {
        "id": vendor.id,
        "name": vendor.name,
        "normalized_name": vendor.normalized_name,
        "transaction_count": vendor.transaction_count,
        "total_amount_by_currency": {
            currency: str(amount) for currency, amount in vendor.total_amount_by_currency.items()
        },
    }


def _vendor_network_response(network: VendorNetwork) -> dict[str, object]:
    return {
        **_vendor_response(network.vendor),
        "transactions": [
            {
                "transaction_id": t.transaction_id,
                "amount": str(t.amount),
                "currency": t.currency,
                "transaction_date": t.transaction_date.isoformat(),
            }
            for t in network.transactions
        ],
    }


@router.post("/v1/engagements/{engagement_id}/knowledge-graph/resolve")
async def resolve_vendors(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    kg_service: KnowledgeGraphService = Depends(get_knowledge_graph_service),
) -> dict[str, object]:
    """Resolves vendor entities from the engagement's current transactions into the graph —
    reads ``risk.transactions``, groups by normalized vendor name, and upserts Vendor/Transaction
    nodes and PAID edges into Neo4j. Idempotent and self-healing: safe to call again after
    importing more transactions (see ``KnowledgeGraphService.resolve_vendors``'s docstring for
    exactly what "self-healing" means here)."""
    newly_resolved_count = await kg_service.resolve_vendors(engagement_id=membership.engagement_id)
    return {"newly_resolved_count": newly_resolved_count}


@router.get("/v1/engagements/{engagement_id}/knowledge-graph/vendors")
async def list_vendors(
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    kg_service: KnowledgeGraphService = Depends(get_knowledge_graph_service),
) -> list[dict[str, object]]:
    """Lists every resolved vendor for this engagement with aggregate transaction stats — the
    graph is this context's read model (Neo4j), not the Postgres bridge tables."""
    vendors = await kg_service.list_vendors(engagement_id=membership.engagement_id)
    return [_vendor_response(v) for v in vendors]


@router.get("/v1/engagements/{engagement_id}/knowledge-graph/vendors/{vendor_id}")
async def get_vendor(
    vendor_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    kg_service: KnowledgeGraphService = Depends(get_knowledge_graph_service),
) -> dict[str, object]:
    """The "vendor 360" view (the fraud-investigation persona) — a vendor plus every
    transaction paid to it. A ``vendor_id`` belonging to another engagement is indistinguishable
    from one that doesn't exist at all (404 either way) — Neo4j has no Row-Level Security of
    its own, so this endpoint's isolation depends on the graph query itself always filtering by
    ``engagement_id`` (proven in ``test_knowledge_graph_isolation.py``, not assumed)."""
    network = await kg_service.get_vendor_network(
        engagement_id=membership.engagement_id, vendor_id=vendor_id
    )
    return _vendor_network_response(network)
