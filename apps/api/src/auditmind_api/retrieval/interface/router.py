"""HTTP routes for the retrieval bounded context — keyword and semantic search, plus the
document-embedding trigger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from auditmind_api.identity.domain.entities import EngagementMembership
from auditmind_api.identity.interface.dependencies import require_engagement_member
from auditmind_api.retrieval.application.services import RetrievalService
from auditmind_api.retrieval.domain.entities import SearchResult
from auditmind_api.retrieval.interface.dependencies import get_retrieval_service
from auditmind_api.shared.roles import CAN_AUTHOR_FINDINGS, CAN_READ_FINDINGS

router = APIRouter(tags=["retrieval"])


def _search_result_response(result: SearchResult) -> dict[str, object]:
    return {
        "chunk_id": result.chunk_id,
        "document_id": result.document_id,
        "text": result.text,
        "rank": result.rank,
    }


@router.get("/v1/engagements/{engagement_id}/search")
async def search_chunks(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
) -> list[dict[str, object]]:
    """The non-vector "keyword leg" of the Retrieval Agent (Phase 6 §10) — Postgres full-text
    search over ``ingestion.chunks``, scoped to this engagement. Ranked by ``ts_rank_cd`` over
    the generated ``search_vector`` column. See ``.../search/semantic`` for the
    vector-embedding leg (Increment 08)."""
    results = await retrieval_service.search_chunks(
        engagement_id=membership.engagement_id, query=q, limit=limit
    )
    return [_search_result_response(r) for r in results]


@router.post(
    "/v1/engagements/{engagement_id}/documents/{document_id}/embeddings",
    status_code=201,
)
async def embed_document(
    document_id: str,
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_AUTHOR_FINDINGS)),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
) -> dict[str, object]:
    """Generates and stores BGE-M3 embeddings for every chunk of a document (Increment 08,
    Phase 6 §6) — synchronous with the request rather than the "asynchronous batch job" the
    design envisions, since no job queue exists yet (see the increment doc's deferred
    section). Idempotent: chunks already embedded by this model are skipped, not
    re-embedded, so calling this twice on the same document is safe."""
    embedded_count = await retrieval_service.embed_document(
        engagement_id=membership.engagement_id, document_id=document_id
    )
    return {"document_id": document_id, "embedded_count": embedded_count}


@router.get("/v1/engagements/{engagement_id}/search/semantic")
async def search_semantic(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    membership: EngagementMembership = Depends(require_engagement_member(*CAN_READ_FINDINGS)),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
) -> list[dict[str, object]]:
    """The vector-embedding leg of the Retrieval Agent (Increment 08, Phase 6 §6-7) — ranks
    chunks by cosine similarity between the query's BGE-M3 embedding and each stored chunk
    embedding, scoped to this engagement. Only chunks a prior call to
    ``.../documents/{id}/embeddings`` has embedded are searchable here; an unembedded
    engagement's evidence returns no results, not an error — the same "search what's been
    indexed" behavior the keyword leg already has (nothing errors if `ingestion.chunks` is
    empty either)."""
    results = await retrieval_service.search_semantic(
        engagement_id=membership.engagement_id, query=q, limit=limit
    )
    return [_search_result_response(r) for r in results]
