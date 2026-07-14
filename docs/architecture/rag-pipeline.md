# Document ingestion and retrieval pipeline

What actually happens between a user uploading a document and AI Copilot being able to answer a
question grounded in it. Every step below is real, running code — file paths are given so this
stays checkable against the source rather than drifting into aspiration over time.

## 1. Upload and parsing

`POST /v1/engagements/{id}/documents` (`apps/api/src/auditmind_api/ingestion/interface/router.py`)
accepts a multipart file upload, hashes the content (SHA-256) to dedupe against anything already
uploaded to the same engagement, stores the raw bytes in blob storage (a local filesystem
directory in dev, stands in for Azure Blob Storage), then parses it.

Parsing (`ingestion/infrastructure/parsers.py`) supports exactly two formats today:

- **Plain text** (`text/plain`) — decoded as UTF-8.
- **PDF** (`application/pdf`) — extracted with `pypdf`, reading each page's native text layer.

There is no OCR. A scanned/image-only PDF has no text layer to extract and fails parsing with a
clear error rather than silently producing an empty document. Word documents, spreadsheets, and
other formats aren't accepted at all yet.

## 2. Chunking

`ingestion/application/chunking.py` splits parsed text into retrievable chunks:

- The primary split boundary is the **paragraph** (blank-line-separated blocks of text) — chunking
  never cuts a paragraph in half unless that single paragraph alone exceeds the size budget.
- The budget is **375 words** per chunk (`DEFAULT_TARGET_WORDS`), a labeled approximation of
  roughly 500 tokens for English text — no real tokenizer runs at this stage, so word count is used
  honestly as an approximation rather than presented as exact token sizing.
- A paragraph that exceeds the budget is split further, with a **12% overlap** between the
  resulting pieces so a sentence isn't stranded at a hard boundary.
- A trailing fragment below a 40-word floor gets merged into the previous chunk rather than
  indexed as its own tiny, low-signal chunk.

## 3. Embedding

`POST /v1/engagements/{id}/documents/{document_id}/embeddings`
(`retrieval/interface/router.py`) embeds every chunk of a parsed document. This is a separate,
explicit call from upload — a document can sit in `PARSED` status, chunked but not yet searchable,
until this runs (the AI Copilot document-upload flow calls it automatically right after upload; a
manual UI upload currently requires it to be triggered separately).

The embedding model is **BAAI/bge-m3**, run locally via `sentence-transformers`
(`retrieval/infrastructure/bge_m3_embedding_generator.py`) — there is no external embedding API
call, no network dependency, no per-token cost. Inference is CPU-bound and synchronous, so it's
offloaded to a worker thread rather than blocking the event loop. Output vectors are L2-normalized,
1024-dimensional.

## 4. Storage and indexing

Vectors are stored in `retrieval.chunk_embeddings` (pgvector), one row per `(chunk_id, model_id)`
pair — the `model_id` in the key means a chunk can be re-embedded under a different model later
without deleting the old vectors first. The table carries a denormalized `engagement_id` so
Row-Level Security can scope every query without a join.

The similarity index is **HNSW** with cosine distance ops (`m=16, ef_construction=64` at index
build time; `ef_search=40` set per query — a hand-tuned recall/latency tradeoff, not a framework
default).

## 5. Retrieval

Two separate legs exist, and — worth being explicit about — **they are not fused into a single
ranked result today**:

- **Keyword leg**: `GET /v1/engagements/{id}/search` — Postgres full-text search
  (`ts_rank_cd` over a generated, GIN-indexed `tsvector` column).
- **Semantic leg**: `GET /v1/engagements/{id}/search/semantic` — cosine similarity
  (`1 - (embedding <=> query_embedding)`) against the HNSW index above.

There is no reranking model and no reciprocal-rank-fusion step combining the two legs' scores —
they aren't on comparable scales, so combining them naively would be misleading rather than
useful. `services/agent-orchestrator`'s evidence-retrieval tool is named `hybrid_search` but today
it only calls the semantic leg; the name is aspirational until a real fusion step exists. This is a
known, scoped gap, not a hidden one.

## 6. How AI Copilot uses this

A question AI Copilot can't answer from its own platform-help knowledge routes to the same
`start_run` investigation pipeline every "Investigations" run uses: a Planner decides which
evidence specialists to call, the retrieval specialist calls the semantic-search endpoint above,
and the result is grounded in whatever chunks actually matched — never fabricated. If nothing
relevant was embedded yet, the honest answer is "I wasn't able to reach a confident conclusion with
the evidence available," not a guess.

## Exercising the whole pipeline locally

`sample-data/` at the repo root plus `scripts/seed_sample_data.py` walks every step above against
real documents: upload → parse → chunk → embed → become searchable. See the "Loading realistic
sample data" section of the root `README.md` for how to run it.
