# services/agent-orchestrator

The independently-deployed LangGraph 9-agent investigation runtime and AI Copilot chat service.
See the root [`README.md`](../../README.md) for the full architecture and why this is split out
as its own deployable.

## Running locally

```bash
make agent-setup      # from the repo root — creates .venv, installs dev extras
make agent-migrate    # requires apps/api's identity migration to have run first
make agent-check      # lint + typecheck + unit tests, no database needed
make agent-test       # unit + integration (integration tests skip without AGENT_MIGRATION_DATABASE_URL)
make agent-run        # uvicorn --reload against whatever AGENT_* env vars are already exported
```

Or via Docker Compose from the repo root (brings up Postgres, runs both services' migrations in
order, starts both HTTP services):

```bash
docker compose up -d postgres migrate agent-migrate agent-orchestrator
curl http://localhost:8001/healthz
```

No `ANTHROPIC_API_KEY` is required to build the graph, start the service, or exercise every
non-LLM node — see the increment doc's "environment note" for exactly where the built-vs-
unverifiable-without-a-key line sits.
