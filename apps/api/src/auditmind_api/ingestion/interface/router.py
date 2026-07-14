"""HTTP routes for the ingestion bounded context — document upload, listing, and chunk reads."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, UploadFile

from auditmind_api.identity.domain.entities import EngagementMembership, User
from auditmind_api.identity.interface.dependencies import (
    get_current_db_user,
    require_engagement_member,
)
from auditmind_api.ingestion.application.services import IngestionService
from auditmind_api.ingestion.interface.dependencies import get_ingestion_service
from auditmind_api.shared.errors import ValidationError
from auditmind_api.shared.metrics import documents_ingested_total
from auditmind_api.shared.settings import Settings, get_settings

router = APIRouter(tags=["ingestion"])


@router.post("/v1/engagements/{engagement_id}/documents", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile,
    membership: EngagementMembership = Depends(require_engagement_member()),
    db_user: User = Depends(get_current_db_user),
    ingestion_service: IngestionService = Depends(get_ingestion_service),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Uploads a document to an engagement's evidence store.

    Membership is checked before a single byte of the file is read — an upload from a
    non-member fails on the cheapest possible check, not after storing or parsing anything.
    """
    content_length_header = request.headers.get("content-length")
    if content_length_header and int(content_length_header) > settings.max_upload_size_bytes:
        raise ValidationError(
            f"File exceeds the maximum upload size of {settings.max_upload_size_bytes} bytes."
        )

    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise ValidationError(
            f"File exceeds the maximum upload size of {settings.max_upload_size_bytes} bytes."
        )

    document = await ingestion_service.ingest_document(
        engagement_id=membership.engagement_id,
        filename=file.filename or "unnamed",
        content=content,
        mime_type=file.content_type or "application/octet-stream",
        ingested_by=db_user.id,
    )
    documents_ingested_total.add(1, {"duplicate": document.duplicate_of is not None})

    return {
        "id": document.id,
        "status": document.status.value,
        "original_filename": document.original_filename,
        "duplicate_of": document.duplicate_of,
    }


@router.get("/v1/engagements/{engagement_id}/documents")
async def list_documents(
    membership: EngagementMembership = Depends(require_engagement_member()),
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> list[dict[str, object]]:
    documents = await ingestion_service.list_documents(membership.engagement_id)
    return [
        {
            "id": d.id,
            "original_filename": d.original_filename,
            "status": d.status.value,
            "mime_type": d.mime_type,
            "duplicate_of": d.duplicate_of,
        }
        for d in documents
    ]


@router.get("/v1/engagements/{engagement_id}/documents/{document_id}/chunks")
async def list_document_chunks(
    document_id: str,
    membership: EngagementMembership = Depends(require_engagement_member()),
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> list[dict[str, object]]:
    """Lists a document's chunks.

    No explicit check here confirms ``document_id`` belongs to the ``engagement_id`` in the
    path beyond the membership check itself — if a caller passed a document id from an
    engagement they are *not* a member of, Row-Level Security on ``ingestion.chunks``
    returns zero rows regardless, since no membership row satisfies the policy's
    subquery for that engagement. The database, not this handler, is what actually prevents
    cross-engagement leakage here.
    """
    chunks = await ingestion_service.list_chunks(document_id)
    return [
        {
            "chunk_index": c.chunk_index,
            "text": c.text,
            "char_start": c.char_start,
            "char_end": c.char_end,
        }
        for c in chunks
    ]
