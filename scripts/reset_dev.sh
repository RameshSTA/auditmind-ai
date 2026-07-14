#!/usr/bin/env bash
#
# Wipes the local dev stack's data volumes and rebuilds them from scratch: fresh Postgres/Neo4j/
# blob storage, migrations re-applied, the demo engagement and personas reseeded. Does not touch
# the frontend — once this finishes, start it with `npm run dev` in apps/web (or `make dev`, which
# also does this) and then run `make seed-sample` to load the checked-in sample dataset.
#
# Usage: ./scripts/reset_dev.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BOLD='\033[1m'
GREEN='\033[0;32m'
RED='\033[0;31m'
RESET='\033[0m'

info() { echo -e "${BOLD}==>${RESET} $1"; }
ok()   { echo -e "${GREEN}✓${RESET} $1"; }
fail() { echo -e "${RED}✗${RESET} $1"; exit 1; }

info "Stopping the stack and deleting its data volumes (Postgres, Neo4j, blob storage)..."
docker compose down -v
ok "Volumes removed"

info "Starting Postgres and Neo4j, then applying migrations..."
docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up -d postgres neo4j migrate api
docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up -d agent-migrate agent-orchestrator

info "Waiting for the API to be ready..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/readyz > /dev/null 2>&1; then
    ok "API is ready"
    break
  fi
  if [ "$i" -eq 30 ]; then
    fail "API did not become ready in time. Run 'docker compose logs api' to see why."
  fi
  sleep 2
done

if [ ! -x "apps/api/.venv/bin/python3" ]; then
  fail "apps/api/.venv not found — run 'make setup' first."
fi

info "Seeding the demo engagement and personas..."
AUDITMIND_SEED_DATABASE_URL="postgresql://auditmind:auditmind_local_dev_only@localhost:5433/auditmind" \
  apps/api/.venv/bin/python3 apps/web/scripts/seed_dev.py
ok "Demo engagement and personas seeded"

echo ""
ok "Reset complete."
echo "Next: start the frontend (npm run dev in apps/web, with AUDITMIND_DEV_AUTH=1), then run:"
echo -e "   ${BOLD}make seed-sample${RESET}"
echo "to load the checked-in sample-data/ dataset through the real API."
