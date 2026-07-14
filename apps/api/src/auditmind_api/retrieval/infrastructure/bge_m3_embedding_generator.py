"""Adapter for the ``EmbeddingGenerator`` port: BGE-M3, run locally via ``sentence-transformers``.

**Deviation, documented deliberately:** the longer-term design routes embedding calls through the
same gateway generation calls use, sharing its rate-limit and cost-tracking policy. No such gateway
exists yet — it's Agent Orchestration territory (``services/agent-orchestrator``, still a
placeholder), blocked on an LLM provider decision entirely separate from this context's scope.
Rather than block the vector-embedding leg on that decision, this adapter runs BGE-M3 in-process
via ``sentence-transformers`` — the same "build the buildable slice, defer the infra-gated part"
choice made for OCR/parsing (pure-Python parsers instead of a cloud document-intelligence service).
Swapping this adapter for a gateway-routed one later is exactly that: a swap behind the same
``EmbeddingGenerator`` port, not a redesign.
"""

from __future__ import annotations

from functools import lru_cache

import anyio.to_thread
import numpy as np
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

_MODEL_ID = "BAAI/bge-m3"


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    """Loads the model once per process, not once per request — this is a multi-hundred-MB model
    load, the same "expensive singleton" treatment ``shared/database.py``'s engine gets, here
    applied to a model instead of a connection pool."""
    model: SentenceTransformer = SentenceTransformer(_MODEL_ID)
    return model


class BgeM3EmbeddingGenerator:
    model_id = _MODEL_ID

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # sentence-transformers' .encode() is synchronous, CPU-bound PyTorch inference — running
        # it directly on the event loop would block every other in-flight request for however
        # long inference takes. Offloaded to a worker thread, the same pattern FastAPI itself uses
        # for sync dependencies.
        embeddings = await anyio.to_thread.run_sync(self._encode, texts)
        return [vector.tolist() for vector in embeddings]

    def _encode(self, texts: list[str]) -> NDArray[np.float32]:
        model = _load_model()
        result: NDArray[np.float32] = model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True
        )
        return result
