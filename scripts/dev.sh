#!/usr/bin/env bash
#
# One-command local dev startup (macOS). Brings up Postgres + Neo4j + the API (with the dev-auth
# overlay so the frontend's dev sign-in actually authenticates), seeds the fixed demo engagement
# and personas, then runs the frontend dev server in the foreground and opens it in the browser.
#
# Usage: ./scripts/dev.sh
# Stop:  Ctrl+C stops the frontend; run ./scripts/stop.sh to also stop the Docker services.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()  { echo -e "${BOLD}==>${RESET} $1"; }
ok()    { echo -e "${GREEN}✓${RESET} $1"; }
warn()  { echo -e "${YELLOW}!${RESET} $1"; }
fail()  { echo -e "${RED}✗${RESET} $1"; exit 1; }

# --- 1. Preflight ---
info "Checking Docker..."
if ! docker info > /dev/null 2>&1; then
  fail "Docker isn't running. Start Docker Desktop, then re-run this script."
fi
ok "Docker is running"

command -v npm > /dev/null 2>&1 || fail "npm not found — install Node.js first (https://nodejs.org)."

# --- 2. Backend: Postgres, Neo4j, migrations, API ---
# (Not agent-orchestrator — it has no UI wired to it yet, so starting it wouldn't change what you
# see; it only adds startup time and another thing that could fail. Start it separately with
# `docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up -d agent-migrate
# agent-orchestrator` if you're working on that service specifically.)
info "Starting backend services (Postgres, Neo4j, API)..."
docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up -d \
  postgres neo4j migrate api

info "Waiting for the API to be ready..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/readyz > /dev/null 2>&1; then
    ok "API is ready (http://localhost:8000)"
    break
  fi
  if [ "$i" -eq 30 ]; then
    fail "API did not become ready in time. Run 'docker compose logs api' to see why."
  fi
  sleep 2
done

# --- 3. Seed demo data (idempotent — safe to run every time) ---
# Needs a Python with `psycopg` importable. The plain system `python3` on macOS usually doesn't
# have it; apps/api's own venv already does (a real dependency of that app, not installed just for
# this). Falls back to creating that venv via `make setup` if it doesn't exist yet, rather than
# silently failing the seed step the way an earlier version of this script did.
if [ -x "apps/api/.venv/bin/python3" ]; then
  SEED_PYTHON="apps/api/.venv/bin/python3"
elif command -v python3.12 > /dev/null 2>&1; then
  info "apps/api/.venv not found — creating it once (needed to seed demo data)..."
  make setup > /tmp/auditmind_setup.log 2>&1 || fail "Setup failed — see /tmp/auditmind_setup.log"
  SEED_PYTHON="apps/api/.venv/bin/python3"
else
  warn "No apps/api/.venv and no python3.12 found — skipping demo data seed."
  warn "Install Python 3.12, then run: make setup && ./scripts/dev.sh"
  SEED_PYTHON=""
fi

if [ -n "$SEED_PYTHON" ]; then
  info "Seeding demo engagement + personas..."
  AUDITMIND_SEED_DATABASE_URL="postgresql://auditmind:auditmind_local_dev_only@localhost:5433/auditmind" \
    "$SEED_PYTHON" apps/web/scripts/seed_dev.py > /tmp/auditmind_seed.log 2>&1 \
    && ok "Demo data seeded" \
    || warn "Seed script reported an issue — see /tmp/auditmind_seed.log (often harmless if data already exists)"
fi

# --- 4. Frontend deps ---
if [ ! -d "apps/web/node_modules" ]; then
  info "Installing frontend dependencies (first run only)..."
  (cd apps/web && npm install)
fi

# --- 5. Open the browser once the frontend is up, then run the dev server in the foreground ---
(
  for i in $(seq 1 30); do
    if curl -sf http://localhost:3000 > /dev/null 2>&1; then
      open "http://localhost:3000"
      break
    fi
    sleep 1
  done
) &

echo ""
ok "Backend is up. Starting the frontend — this terminal will show its logs."
echo -e "   ${BOLD}http://localhost:3000${RESET} will open automatically in a moment."
echo -e "   Press ${BOLD}Ctrl+C${RESET} to stop the frontend. Run ${BOLD}./scripts/stop.sh${RESET} to also stop the backend."
echo ""

cd apps/web
AUDITMIND_DEV_AUTH=1 npm run dev
