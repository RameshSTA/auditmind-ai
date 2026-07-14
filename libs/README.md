# libs

Shared code that will eventually be used across `apps/api`, `services/agent-orchestrator`, and CI
tooling — not created yet, on purpose. `services/agent-orchestrator` exists now (Increment 12) as
a second deployable, but it deliberately does **not** share code with `apps/api`: it re-implements
its own minimal JWT auth, settings, and RFC 7807 error envelope rather than importing `apps/api`'s
(see that increment's doc §2). Nothing currently asks to share code between the two, so extracting
any would be premature abstraction (Phase 3 coding standards, §3) — empty subdirectories sitting
on disk for a need that doesn't exist yet is exactly the clutter that principle warns against, so
none are created until a real second consumer shows up. When one does, this is where it goes:

- `domain/` — shared entities, value objects, and ports (interfaces).
- `infrastructure/` — shared adapters (Postgres, pgvector, Neo4j, LiteLLM clients).
- `contracts/` — OpenAPI specs and shared event/DTO schemas.
