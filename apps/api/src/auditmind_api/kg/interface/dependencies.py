"""FastAPI dependencies wiring the Knowledge Graph context into the request lifecycle."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auditmind_api.kg.application.services import KnowledgeGraphService
from auditmind_api.kg.infrastructure.entity_candidate_repository import (
    PostgresEntityCandidateRepository,
)
from auditmind_api.kg.infrastructure.neo4j_graph_store import Neo4jGraphStore
from auditmind_api.kg.infrastructure.transaction_source import PostgresTransactionSource
from auditmind_api.shared.database import get_db_session
from auditmind_api.shared.neo4j import get_neo4j_driver


def get_knowledge_graph_service(
    session: AsyncSession = Depends(get_db_session),
) -> KnowledgeGraphService:
    return KnowledgeGraphService(
        transaction_source=PostgresTransactionSource(session),
        candidate_repository=PostgresEntityCandidateRepository(session),
        graph_store=Neo4jGraphStore(get_neo4j_driver()),
    )
