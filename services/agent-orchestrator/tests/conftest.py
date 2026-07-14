"""Shared pytest fixtures.

The RSA keypair fixtures mirror ``apps/api/tests/conftest.py`` exactly (same technique, same
reason: sign and validate real JWTs locally without a network call to a live Entra ID tenant).
``FakeLlmClient`` is this service's equivalent of a fake adapter behind the ``LlmClient`` port —
every test that exercises a node, the graph, or the orchestration service uses it instead of the
real LiteLLM gateway, so the whole stack above the gateway adapter is verified with no API key.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from agent_orchestrator.domain.entities import ModelResponse, PromptMessage, RetrievedChunk

TEST_AUDIENCE = "test-agent-orchestrator-client"
TEST_ISSUER = "https://login.microsoftonline.com/test-tenant/v2.0"
TEST_KID = "test-key-1"


@dataclass(frozen=True)
class KeyMaterial:
    private_pem: str
    jwk_dict: dict[str, Any]


@pytest.fixture(scope="session")
def rsa_keypair() -> KeyMaterial:
    """A locally-generated RSA keypair, standing in for an Entra ID signing key."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    jwk_dict = jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kid"] = TEST_KID
    jwk_dict["alg"] = "RS256"

    return KeyMaterial(private_pem=private_pem, jwk_dict=jwk_dict)


def make_token(
    rsa_keypair: KeyMaterial,
    *,
    subject: str = "entra-user-1",
    roles: list[str] | None = None,
    audience: str = TEST_AUDIENCE,
    issuer: str = TEST_ISSUER,
    expires_in_seconds: int = 3600,
    kid: str = TEST_KID,
) -> str:
    """Signs a test access token with the fixture's private key."""
    now = int(time.time())
    claims = {
        "sub": subject,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in_seconds,
        "roles": roles or ["Auditor"],
        "tid": "test-tenant",
    }
    return jwt.encode(claims, rsa_keypair.private_pem, algorithm="RS256", headers={"kid": kid})


@dataclass
class FakeLlmClient:
    """A fake :class:`~agent_orchestrator.domain.ports.LlmClient` — every test above the gateway
    adapter layer uses this instead of a real model call, the same "real integration against the
    port, fake behind it" pattern every other bounded context's tests already use for their own
    ports (e.g. ``apps/api``'s fake ``EmbeddingGenerator``).

    ``responses`` lets a test script a specific reply per call (by call index); with none queued,
    ``default_text`` is returned every time — enough to drive a node or the whole graph through a
    deterministic path without asserting on exact wording.

    ``smart_defaults`` is for callers that don't care about LLM content at all (the HTTP-integration
    suites, which only exercise RBAC/RLS/HITL plumbing) and so never script ``responses``: once the
    queue is empty, it routes each call to a response its own node can actually parse — a JSON plan
    for the Planner, a numeric score for the Evaluation judge — keyed off each node's system framing
    (`nodes._SYSTEM_FRAMING`/`nodes._EVALUATION_SYSTEM_PROMPT`) rather than call order. Without it
    (the default, and what every node/graph/orchestrator unit test uses), ``default_text`` is
    returned verbatim, so a test that deliberately sets e.g. ``default_text="0.6"`` to exercise the
    evaluation parser still gets exactly that back.
    """

    default_text: str = "synthesized draft"
    responses: list[str] = field(default_factory=list)
    smart_defaults: bool = False
    calls: list[tuple[str, int]] = field(default_factory=list)
    message_calls: list[list[PromptMessage]] = field(default_factory=list)

    async def complete(
        self, *, messages: list[PromptMessage], model: str, max_tokens: int = 1024
    ) -> ModelResponse:
        self.calls.append((model, len(messages)))
        self.message_calls.append(messages)
        if self.responses:
            text = self.responses.pop(0)
        elif self.smart_defaults:
            text = self._contextual_default(messages)
        else:
            text = self.default_text
        return ModelResponse(text=text, model=model, input_tokens=10, output_tokens=10)

    def _contextual_default(self, messages: list[PromptMessage]) -> str:
        system_content = next((m.content for m in messages if m.role == "system"), "")
        if "Decompose the task" in system_content:
            return '["retrieval"]'
        if "strict evaluator" in system_content:
            return "0.95"
        return self.default_text

    @property
    def last_user_content(self) -> str:
        """The content of the most recent call's ``user`` message — what a test inspects to
        confirm a node actually grounded its prompt in real tool-fetched data, not just the task
        string."""
        messages = self.message_calls[-1]
        return next(m.content for m in reversed(messages) if m.role == "user")


@dataclass
class FakeApiClient:
    """A fake :class:`~agent_orchestrator.domain.ports.RagApiClient` — the same fakes-over-ports
    discipline as :class:`FakeLlmClient`, so node/graph/orchestrator tests exercise the real RAG
    wiring (a node really calls ``self._api.semantic_search(...)`` and gets back real-shaped
    :class:`RetrievedChunk` values) with no network call to apps/api.

    ``chunks``/``anomalies``/``risk_scores``/``vendors`` are canned responses a test pre-seeds;
    ``submitted`` records every ``submit_finding_draft`` call so a test can assert a finding was
    (or wasn't) written, and with which evidence.
    """

    chunks: list[RetrievedChunk] = field(default_factory=list)
    anomalies: list[dict[str, object]] = field(default_factory=list)
    risk_scores: list[dict[str, object]] = field(default_factory=list)
    vendors: list[dict[str, object]] = field(default_factory=list)
    submitted: list[dict[str, object]] = field(default_factory=list)
    next_finding_id: str = "finding-1"

    async def semantic_search(
        self, *, engagement_id: str, query: str, limit: int = 5
    ) -> list[RetrievedChunk]:
        return self.chunks

    async def submit_finding_draft(
        self,
        *,
        engagement_id: str,
        title: str,
        description: str,
        severity: str,
        evidence: list[RetrievedChunk],
    ) -> str:
        self.submitted.append(
            {
                "engagement_id": engagement_id,
                "title": title,
                "description": description,
                "severity": severity,
                "evidence": evidence,
            }
        )
        return self.next_finding_id

    async def list_anomalies(self, *, engagement_id: str) -> list[dict[str, object]]:
        return self.anomalies

    async def list_risk_scores(self, *, engagement_id: str) -> list[dict[str, object]]:
        return self.risk_scores

    async def list_vendors(self, *, engagement_id: str) -> list[dict[str, object]]:
        return self.vendors
