"""Adapter for the ``EntityCandidateRepository`` port: reads and writes the two tables this
context owns (``kg.entity_candidates``, ``kg.entity_resolution_map``, Phase 4 §4). Goes through
the ORM — the same convention every other context uses for a table it owns (e.g. ``ingestion``'s
``PostgresChunkRepository``), unlike the raw-SQL-only adapters this context uses to read *another*
context's table (``transaction_source.py``).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.kg.infrastructure.models import EntityCandidateModel, EntityResolutionMapModel

_VENDOR_ENTITY_TYPE = "vendor"


class PostgresEntityCandidateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_existing_vendor_id(
        self, *, engagement_id: str, normalized_name: str
    ) -> str | None:
        result = await self._session.execute(
            select(EntityResolutionMapModel.neo4j_entity_id)
            .join(
                EntityCandidateModel,
                EntityCandidateModel.id == EntityResolutionMapModel.candidate_id,
            )
            .where(
                EntityCandidateModel.engagement_id == engagement_id,
                EntityCandidateModel.normalized_name == normalized_name,
                EntityCandidateModel.entity_type == _VENDOR_ENTITY_TYPE,
            )
            .limit(1)
        )
        neo4j_entity_id = result.scalar_one_or_none()
        return str(neo4j_entity_id) if neo4j_entity_id is not None else None

    async def record_candidate_and_resolution(
        self,
        *,
        engagement_id: str,
        source_transaction_id: str,
        raw_name: str,
        normalized_name: str,
        neo4j_entity_id: str,
    ) -> bool:
        candidate_id = uuid.uuid4()
        insert_candidate = (
            insert(EntityCandidateModel)
            .values(
                id=candidate_id,
                engagement_id=engagement_id,
                source_transaction_id=source_transaction_id,
                entity_type=_VENDOR_ENTITY_TYPE,
                raw_name=raw_name,
                normalized_name=normalized_name,
                status="resolved",
            )
            .on_conflict_do_nothing(
                index_elements=["source_transaction_id", "entity_type"]
            )
            .returning(EntityCandidateModel.id)
        )
        result = await self._session.execute(insert_candidate)
        inserted_id = result.scalar_one_or_none()
        if inserted_id is None:
            # A candidate for this (transaction, entity_type) already exists — its resolution
            # row was written alongside it in whichever earlier run created it (both writes
            # always happen together, see below), so there's nothing new to record here.
            return False

        await self._session.execute(
            insert(EntityResolutionMapModel).values(
                candidate_id=inserted_id,
                neo4j_entity_id=neo4j_entity_id,
                confidence=Decimal("1.0"),
            )
        )
        await self._session.flush()
        return True
