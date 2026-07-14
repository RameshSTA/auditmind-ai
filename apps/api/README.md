# apps/api

The core FastAPI backend — a modular monolith of eight bounded contexts (`identity`, `ingestion`,
`retrieval`, `reporting`, `risk`, `audit_trail`, `kg`), each following hexagonal architecture
(`domain` → `application` → `infrastructure` → `interface`), enforced in CI by import-linter, not
just convention. See the root [`README.md`](../../README.md) for the full architecture and the
fastest way to run the whole stack (`make dev`).

## Running locally

```bash
make setup         # from the repo root — creates .venv, installs dev extras
make migrate       # requires AUDITMIND_MIGRATION_DATABASE_URL / AUDITMIND_APP_DB_PASSWORD (see root README)
make check         # lint + typecheck + unit tests, no database needed
make test          # unit + integration (integration tests skip without a database)
make run           # uvicorn --reload against whatever AUDITMIND_* env vars are already exported
```

Or via Docker Compose from the repo root (brings up Postgres, Neo4j, runs migrations, starts the
API):

```bash
docker compose up -d postgres neo4j migrate api
curl http://localhost:8000/readyz
```

For the fastest path to a fully working local stack — this service plus the frontend, with demo
data seeded — use `make dev` from the repo root instead of the commands above.
