# apps/web

Next.js 15 App Router frontend and BFF — the auditor workspace UI. See the root
[`README.md`](../../README.md) for the full architecture.

## Running locally

```bash
# 1. Bring up the backend with the dev-auth overlay (points the API's Entra config at this app's
#    dev JWKS endpoint instead of a real tenant):
docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up -d postgres neo4j migrate api

# 2. Seed fixed demo identities + engagement membership:
AUDITMIND_SEED_DATABASE_URL="postgresql://auditmind:auditmind_local_dev_only@localhost:5433/auditmind" \
  python scripts/seed_dev.py

# 3. Run the dev server:
AUDITMIND_DEV_AUTH=1 npm run dev
```

Then open http://localhost:3000 and pick a persona.
