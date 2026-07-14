"""Domain entities for the Retrieval context, covering both the keyword leg and the
vector-embedding leg.

Plain, framework-free dataclasses — the same convention every prior context established.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """A ranked chunk match. Shared by both the keyword leg (``rank`` is ``ts_rank_cd``, unbounded)
    and the vector-embedding leg (``rank`` is cosine similarity, roughly ``[-1, 1]``) — the two
    legs' scores are not on a comparable scale to each other, deliberately: hybrid ranking that
    would combine them is not buildable until both legs exist, so nothing here pretends the two
    are already unified."""

    chunk_id: str
    document_id: str
    engagement_id: str
    text: str
    rank: float


@dataclass(frozen=True)
class ChunkText:
    """The minimal shape this context needs from a chunk it does not own, to embed it — an id to
    key the resulting embedding row by, and the text to embed. Deliberately not `ingestion`'s own
    `Chunk` entity — a bounded context never imports another's domain model."""

    chunk_id: str
    text: str
