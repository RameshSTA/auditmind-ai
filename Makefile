.PHONY: dev stop setup up down migrate test test-unit test-integration lint typecheck check fmt run logs \
	agent-setup agent-migrate agent-test agent-test-unit agent-test-integration agent-lint \
	agent-typecheck agent-check agent-fmt agent-run reset-dev seed-sample

API := apps/api
VENV := $(API)/.venv
PY := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

AGENT := services/agent-orchestrator
AGENT_VENV := $(AGENT)/.venv
AGENT_PIP := $(AGENT_VENV)/bin/pip

## One-command local dev: backend via Docker, demo data seeded, frontend running, browser opened.
## This is the "just make it work" entrypoint — see scripts/dev.sh for what it actually does.
dev:
	./scripts/dev.sh

## Stops the Docker services `make dev` starts (data volumes persist).
stop:
	./scripts/stop.sh

## Create apps/api/.venv and install dependencies (dev extras).
setup:
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -e "$(API)[dev]" -q

## Start Postgres (pgvector) + Neo4j + run migrations + start the API via Docker Compose.
up:
	docker compose up --build -d

## Stop and remove the Docker Compose stack (data volumes persist).
down:
	docker compose down

## Apply Alembic migrations against AUDITMIND_MIGRATION_DATABASE_URL (see README for local values).
migrate:
	cd $(API) && PYTHONPATH=src .venv/bin/alembic upgrade head

## Run the full test suite (unit + integration; integration tests skip without a database).
test:
	cd $(API) && PYTHONPATH=src .venv/bin/pytest tests -v

test-unit:
	cd $(API) && PYTHONPATH=src .venv/bin/pytest tests/unit -v

test-integration:
	cd $(API) && PYTHONPATH=src .venv/bin/pytest tests/integration -v

## Ruff + import-linter (the two static checks CI's `lint` job runs).
lint:
	cd $(API) && .venv/bin/ruff check src tests migrations
	cd $(API) && PYTHONPATH=src .venv/bin/lint-imports

typecheck:
	cd $(API) && .venv/bin/mypy src

## Everything CI runs, in order, without a database (unit tests only).
check: lint typecheck test-unit

fmt:
	cd $(API) && .venv/bin/ruff check --fix src tests migrations

## Run the API locally against whatever AUDITMIND_* env vars are already exported.
run:
	cd $(API) && PYTHONPATH=src .venv/bin/uvicorn auditmind_api.main:app --reload

## Wipe all local data volumes and rebuild from scratch (fresh Postgres/Neo4j, migrations
## reapplied, demo engagement + personas reseeded). Frontend is left for you to start; run
## `make seed-sample` afterwards to load sample-data/ once it's up.
reset-dev:
	./scripts/reset_dev.sh

## Load sample-data/ (documents, transactions) into the demo engagement through the real API.
## Requires the backend (`make reset-dev` or `make dev`) and frontend (`npm run dev`, apps/web)
## both running first.
seed-sample:
	$(PY) scripts/seed_sample_data.py

logs:
	docker compose logs -f

# --- services/agent-orchestrator (ADR-001 separate deployable) ---
# Mirrors the apps/api targets above exactly, one section down, because the two are independently
# built/tested/deployed services sharing this repo and this Makefile, not one bounded context of
# the other.

## Create services/agent-orchestrator/.venv and install dependencies (dev extras).
agent-setup:
	python3.12 -m venv $(AGENT_VENV)
	$(AGENT_PIP) install --upgrade pip -q
	$(AGENT_PIP) install -e "$(AGENT)[dev]" -q

## Apply Alembic migrations against AGENT_MIGRATION_DATABASE_URL (see README for local values).
## Requires apps/api's identity migration to have run first (agent.* tables FK into identity.*).
agent-migrate:
	cd $(AGENT) && PYTHONPATH=src .venv/bin/alembic upgrade head

agent-test:
	cd $(AGENT) && PYTHONPATH=src .venv/bin/pytest tests -v

agent-test-unit:
	cd $(AGENT) && PYTHONPATH=src .venv/bin/pytest tests/unit -v

agent-test-integration:
	cd $(AGENT) && PYTHONPATH=src .venv/bin/pytest tests/integration -v

agent-lint:
	cd $(AGENT) && .venv/bin/ruff check src tests migrations
	cd $(AGENT) && PYTHONPATH=src .venv/bin/lint-imports

agent-typecheck:
	cd $(AGENT) && .venv/bin/mypy src

agent-check: agent-lint agent-typecheck agent-test-unit

agent-fmt:
	cd $(AGENT) && .venv/bin/ruff check --fix src tests migrations

## Run the service locally against whatever AGENT_* env vars are already exported.
agent-run:
	cd $(AGENT) && PYTHONPATH=src .venv/bin/uvicorn agent_orchestrator.main:app --reload
