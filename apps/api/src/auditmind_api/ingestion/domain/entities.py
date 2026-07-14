"""Domain entities for the Ingestion context.

Plain, framework-free dataclasses — no SQLAlchemy, no FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DocumentStatus(str, Enum):
    RECEIVED = "received"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


@dataclass(frozen=True)
class Document:
    id: str
    engagement_id: str
    source_connector: str
    original_filename: str
    storage_uri: str
    sha256_hash: str
    mime_type: str
    status: DocumentStatus
    ingested_at: datetime
    ingested_by: str
    duplicate_of: str | None = None


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    engagement_id: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int
    created_at: datetime
