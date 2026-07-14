#!/usr/bin/env bash
#
# Stops the Docker services scripts/dev.sh starts. Data volumes persist (Postgres/Neo4j data
# survives) — this stops containers, it doesn't delete anything. The frontend dev server itself
# isn't managed here; Ctrl+C in its terminal stops that.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Stopping backend services..."
docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml down
echo "Done. Data volumes are untouched — next ./scripts/dev.sh run picks up where this left off."
