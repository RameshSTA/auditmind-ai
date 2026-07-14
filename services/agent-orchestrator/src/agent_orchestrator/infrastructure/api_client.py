"""HTTP adapter for the RAG tool ports (:class:`ApiSearchClient`, :class:`FindingWriter`) —
closes the named gap: nodes previously built prompts from the raw task string only, and no tool
call ever wrote an agent's draft back into apps/api's reporting context.

Forwards the *same bearer token* the caller's own request to this service already carried
(``shared/auth.get_bearer_token``) — this adapter mints nothing, holds no service credential of
its own. apps/api validates that token exactly as it would for any other caller, and its own
RLS/engagement-membership checks are what actually enforce access; this class is a thin transport,
not a second authorization layer.
"""

from __future__ import annotations

from typing import Any

import httpx

from agent_orchestrator.domain.entities import RetrievedChunk
from agent_orchestrator.shared.errors import UpstreamApiError
from agent_orchestrator.shared.logging import get_logger

logger = get_logger(__name__)

# BGE-M3 (retrieval's embedding model, apps/api) is CPU-bound in this environment — encoding even
# one short query took ~35s in practice (confirmed against the real service, not assumed), well
# past a typical API timeout. This client waits long enough for that real latency rather than
# timing out a call that was actually going to succeed; the finding-write calls below are ordinary
# fast Postgres writes and don't need nearly this much headroom, but share the same budget for
# simplicity since a slow search is the dominant real-world case this client has to tolerate.
_TIMEOUT_SECONDS = 60.0


class HttpApiClient:
    def __init__(self, *, base_url: str, bearer_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"authorization": f"Bearer {bearer_token}"}

    async def semantic_search(
        self, *, engagement_id: str, query: str, limit: int = 5
    ) -> list[RetrievedChunk]:
        url = f"{self._base_url}/v1/engagements/{engagement_id}/search/semantic"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url, params={"q": query, "limit": limit}, headers=self._headers
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("rag_search_failed", engagement_id=engagement_id, error=str(exc))
            raise UpstreamApiError(
                "Could not reach apps/api's semantic search endpoint for real evidence."
            ) from exc

        return [
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                text=row["text"],
                rank=row["rank"],
            )
            for row in response.json()
        ]

    async def submit_finding_draft(
        self,
        *,
        engagement_id: str,
        title: str,
        description: str,
        severity: str,
        evidence: list[RetrievedChunk],
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                finding_response = await client.post(
                    f"{self._base_url}/v1/engagements/{engagement_id}/findings",
                    json={"title": title, "description": description, "severity": severity},
                    headers=self._headers,
                )
                finding_response.raise_for_status()
                finding_id = str(finding_response.json()["id"])

                for chunk in evidence:
                    # A quoted snippet, not the chunk id alone — AttachEvidenceRequest.citation_text
                    # is what a reviewer reads on the Findings panel to judge the citation without
                    # opening the source document; the id alone would tell them nothing.
                    snippet = chunk.text[:280]
                    evidence_response = await client.post(
                        f"{self._base_url}/v1/engagements/{engagement_id}/findings/"
                        f"{finding_id}/evidence",
                        json={"chunk_id": chunk.chunk_id, "citation_text": snippet},
                        headers=self._headers,
                    )
                    evidence_response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("rag_finding_write_failed", engagement_id=engagement_id, error=str(exc))
            raise UpstreamApiError(
                "Could not write this run's draft finding back to apps/api."
            ) from exc

        return finding_id

    async def list_anomalies(self, *, engagement_id: str) -> list[dict[str, object]]:
        return await self._get_json(f"/v1/engagements/{engagement_id}/anomalies")

    async def list_risk_scores(self, *, engagement_id: str) -> list[dict[str, object]]:
        return await self._get_json(f"/v1/engagements/{engagement_id}/risk-scores")

    async def list_vendors(self, *, engagement_id: str) -> list[dict[str, object]]:
        return await self._get_json(f"/v1/engagements/{engagement_id}/knowledge-graph/vendors")

    # --- AI Copilot direct actions (application/copilot_chat_service.py's action dispatcher) ---
    # Each wraps exactly one confirmed, synchronous apps/api endpoint the real UI pages already
    # call — same auth model (the caller's own bearer token), same request/response shapes, no new
    # endpoint invented on the apps/api side for any of these.

    async def import_transactions(
        self, *, engagement_id: str, transactions: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = await self._post_json(
            f"/v1/engagements/{engagement_id}/transactions",
            json={"transactions": transactions},
        )
        return result

    async def run_risk_scan(self, *, engagement_id: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = await self._post_json(
            f"/v1/engagements/{engagement_id}/risk/scan", json={}
        )
        return result

    async def compute_risk_scores(self, *, engagement_id: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = await self._post_json(
            f"/v1/engagements/{engagement_id}/risk/score", json=None
        )
        return result

    async def resolve_vendors(self, *, engagement_id: str) -> dict[str, object]:
        result = await self._post_json(
            f"/v1/engagements/{engagement_id}/knowledge-graph/resolve", json=None
        )
        return result if isinstance(result, dict) else {}

    async def generate_report(self, *, engagement_id: str) -> dict[str, object]:
        result = await self._post_json(f"/v1/engagements/{engagement_id}/reports", json=None)
        return result if isinstance(result, dict) else {}

    async def generate_embeddings(
        self, *, engagement_id: str, document_id: str
    ) -> dict[str, object]:
        result = await self._post_json(
            f"/v1/engagements/{engagement_id}/documents/{document_id}/embeddings", json=None
        )
        return result if isinstance(result, dict) else {}

    async def _get_json(self, path: str) -> list[dict[str, object]]:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers=self._headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("rag_context_read_failed", path=path, error=str(exc))
            raise UpstreamApiError(f"Could not read context from apps/api ({path}).") from exc
        result: list[dict[str, object]] = response.json()
        return result

    async def _post_json(self, path: str, *, json: object) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=json, headers=self._headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("copilot_action_call_failed", path=path, error=str(exc))
            raise UpstreamApiError(
                f"Could not complete this action against apps/api ({path})."
            ) from exc
        return response.json()
