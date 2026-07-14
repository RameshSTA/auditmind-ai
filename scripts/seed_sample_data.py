#!/usr/bin/env python3
"""Loads sample-data/ into a running engagement through the real apps/api endpoints.

Uploads every document under sample-data/documents/, generates embeddings for each, imports the
transactions in sample-data/transactions.json, then runs vendor resolution, the anomaly scan, risk
scoring, and report generation - the same sequence a user would trigger by hand through the UI or
AI Copilot. Nothing here writes to Postgres directly; every row that ends up in the database went
through the same validation and chunking/embedding pipeline a real upload would.

Prerequisites:
  - The stack must be up: `docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml
    up -d postgres neo4j migrate api`.
  - apps/web/scripts/seed_dev.py must have already run, so the target engagement and the
    dev-auditor persona exist.
  - `npm run dev` must be running in apps/web, since the API validates bearer tokens against
    apps/web's dev-auth JWKS endpoint (see docker-compose.dev-auth.yml) - this script mints a
    token compatible with that JWKS, but the API still has to be able to fetch it.

Usage:
  apps/api/.venv/bin/python scripts/seed_sample_data.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from jose import jwt

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = REPO_ROOT / "sample-data"
API_BASE_URL = "http://localhost:8000"
ENGAGEMENT_ID = "00000000-0000-0000-0000-0000000000e1"

# The same throwaway dev signing key apps/web/src/server/dev-auth.ts uses, so tokens minted here
# validate against the same JWKS the API is pointed at in dev. Never used outside AUDITMIND_DEV_AUTH.
DEV_PRIVATE_KEY_PKCS8 = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCyKGQ41kMpnQVn
I8pFJHX8+sTfw116DLYvmwytzX6y2+CuQ1FzVDLfWlg/uWf3CQ4QmWUJPbumv+7M
rJWmFEoBm7whReWDmKkXic/NK9PsNvpt8vzZsEdhYYtGQykr+oNSIehTrDpTOuAe
cOWeLPF4ULFa05WEjHTIVJROxaT4fKYLfr80Kqy7Dv5bgoCAMx0DpdG9etKVwNk+
Tjc/J58N2J3wreGJk0KQmuLaqYXFH/wmfFOCJZpZzIk6vwHEuXI3LU9JIfyDrSsN
95R0CwjAt1RrsX+dqTynLBd/2AaAkAJraju0W0y6Z2OIijTXkq0INsukvxnMBuUJ
oL7oUicdAgMBAAECggEAAsnlZmBa38a17sXdeinrxjXJ/sio6L//MA0+FxBdjdYp
lQC3wIA5EExH5QN1cE/4zji1yf294gp6EJkY+ecHu4ZhB6eiQ+Y6q1l8Yl/vwBr3
iOC3UCmqtwhXAkFgiRrcPbJggm1yjO/3Jx1+8p64eY4lyzVUT1AAL0ySSmNcmt5t
3q3iblPIl77kGK8ly/kEc+lRCT69mB/be8tA1PNQ667PtCDouPFRC1ynhRAdJ9GD
qwadYAW3uYuoMpwHaFbG53zI4TXB+VqPAA0QpszEAE7hkllAC5qa8ogwMejYJl/Y
L5bTH/NYpHRUobbX5EmAQZzqK7fVU4Yj0/t3XxzxTwKBgQD038zWkkSLNqlXjRKU
4LODqtvBWfZA/BF8noXaHx5/SGT8zeGrJOJn2S0cYuKP/UDVFRoiX9B4A0eQAoCD
VdWjIgPDJSfpaTqEgjgdci06iS/Av85YGnA7zLqkrbWCNboaZrBRHRlJg/aWvDzV
p8YlpuxkwDUoplnGJsETeipJ0wKBgQC6QJgNbiV0R0K2QosVudTBwClysoP8JtHF
8H7ha7JPz4AtUQ/Lnx4+viijD247IcBrWi65uvPpv5xe7edlQuGAkuPUmHE7YNr1
az+ErukgbSE080Ms9LtXKhmXOy/aYAyLHjg4V1Z+iqvsMGEaByYGP9lzK75g8Od4
R2lq+/LFTwKBgF9R6uOvpjzmtz6cbJpFabucO9TlFwWu2YPAFWyV5oI3hRAfeHPt
dLBmCrhdCcJxG9aWU0kEMRs2c5nsT2hQdkv9RqelBAdI7f18zykvM8nwcwU95K3J
BN1SUWkfMWORVHNIe+PnRtumIcwFVEz69RfdBXImm8rKDnIizc+uI13/AoGAQn10
yraboFsgMbintmXU0iYrpcqc25NwJ92nLgooad2FwKfDn8l6HqP8FdoYW/u8mZZk
P+HB2ZyR0kHT7Y5mumO1+dtB8RSulhZnYpKenvjWdfSx8oabqo5Y/GgguTC4yaFM
KLDlK9+NaJAM2iHbLTf2BOuE106pE4NK2up+zHkCgYEAtrWGbSi9ugVG5iDlCCnt
vgB1CQ7yawuFBlAOnVD758beAdFVpvsgQw2gQ8j3henbAV+p0X6lKtmIxycFhe/4
ZfJLyIWdJ6RGu2sf6z6ZSdK3rhWc5yNMpiiYPph5iflhA8lPQ5BkLJ8AKARGX+PO
ERYu5VnLnZeGmIwVt4Ekd7g=
-----END PRIVATE KEY-----"""

DEV_KID = "auditmind-dev-key-1"
DEV_ISSUER = "https://login.microsoftonline.com/auditmind-dev-tenant/v2.0"
DEV_AUDIENCE = "auditmind-dev-api"
DEV_TENANT = "auditmind-dev-tenant"
DEV_AUDITOR_SUBJECT = "dev-auditor"


def mint_dev_token(subject: str = DEV_AUDITOR_SUBJECT, roles: list[str] | None = None) -> str:
    """Mints an RS256 bearer token equivalent to apps/web's mintDevToken(), for the same
    persona a browser session would use, so this script hits the exact same authorization
    checks a real user does."""
    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub": subject,
        "aud": DEV_AUDIENCE,
        "iss": DEV_ISSUER,
        "iat": now,
        "exp": now + timedelta(hours=8),
        "roles": roles or ["Auditor"],
        "tid": DEV_TENANT,
    }
    return jwt.encode(claims, DEV_PRIVATE_KEY_PKCS8, algorithm="RS256", headers={"kid": DEV_KID})


def check_prerequisites(client: httpx.Client) -> None:
    try:
        response = client.get(f"{API_BASE_URL}/healthz", timeout=5)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        sys.exit(
            f"Cannot reach apps/api at {API_BASE_URL} ({exc}). Start the stack first: "
            "docker compose -f docker-compose.yml -f docker-compose.dev-auth.yml up -d "
            "postgres neo4j migrate api"
        )
    try:
        jwks_response = httpx.get("http://localhost:3000/api/dev-auth/jwks.json", timeout=5)
        jwks_response.raise_for_status()
    except httpx.HTTPError as exc:
        sys.exit(
            f"Cannot reach the dev-auth JWKS endpoint on apps/web ({exc}). apps/api validates "
            "every bearer token against it, so `npm run dev` must be running in apps/web before "
            "this script's tokens will be accepted."
        )


def upload_documents(client: httpx.Client, headers: dict[str, str]) -> None:
    documents_dir = SAMPLE_DATA_DIR / "documents"
    mime_types = {".txt": "text/plain", ".pdf": "application/pdf"}
    uploaded = 0
    for path in sorted(documents_dir.iterdir()):
        mime_type = mime_types.get(path.suffix)
        if mime_type is None:
            continue
        with path.open("rb") as fh:
            response = client.post(
                f"{API_BASE_URL}/v1/engagements/{ENGAGEMENT_ID}/documents",
                headers=headers,
                files={"file": (path.name, fh, mime_type)},
                timeout=30,
            )
        response.raise_for_status()
        document = response.json()
        if document.get("duplicate_of"):
            print(f"  {path.name}: already present (duplicate of {document['duplicate_of']})")
            continue
        embed_response = client.post(
            f"{API_BASE_URL}/v1/engagements/{ENGAGEMENT_ID}/documents/{document['id']}/embeddings",
            headers=headers,
            # The first embedding call in a freshly started API container downloads and loads
            # the BGE-M3 model weights (no persistent model cache volume), which alone can take
            # several minutes on a cold start; subsequent calls in the same process are fast.
            timeout=600,
        )
        embed_response.raise_for_status()
        embedded = embed_response.json()
        print(f"  {path.name}: uploaded, {embedded['embedded_count']} chunks embedded")
        uploaded += 1
    print(f"Documents: {uploaded} newly uploaded and embedded.")


def import_transactions(client: httpx.Client, headers: dict[str, str]) -> None:
    payload = json.loads((SAMPLE_DATA_DIR / "transactions.json").read_text())
    response = client.post(
        f"{API_BASE_URL}/v1/engagements/{ENGAGEMENT_ID}/transactions",
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    print(f"Transactions: imported {len(payload['transactions'])} records.")


def run_analysis_pipeline(client: httpx.Client, headers: dict[str, str]) -> None:
    steps = [
        ("Vendor resolution", "POST", f"/v1/engagements/{ENGAGEMENT_ID}/knowledge-graph/resolve"),
        ("Anomaly scan", "POST", f"/v1/engagements/{ENGAGEMENT_ID}/risk/scan"),
        ("Risk scoring", "POST", f"/v1/engagements/{ENGAGEMENT_ID}/risk/score"),
        ("Report generation", "POST", f"/v1/engagements/{ENGAGEMENT_ID}/reports"),
    ]
    for label, method, path in steps:
        response = client.request(method, f"{API_BASE_URL}{path}", headers=headers, timeout=60)
        response.raise_for_status()
        print(f"  {label}: {response.status_code} {response.json()}")


def main() -> None:
    with httpx.Client() as client:
        check_prerequisites(client)
        headers = {"Authorization": f"Bearer {mint_dev_token()}"}

        print("Uploading and embedding sample documents...")
        upload_documents(client, headers)

        print("Importing sample transactions...")
        import_transactions(client, headers)

        print("Running the analysis pipeline (vendor resolution, scan, scoring, report)...")
        # A short pause lets the transaction import's own request settle before the scan reads
        # the full transaction set back out - the endpoints are synchronous, this just avoids
        # racing a fast local Postgres commit against the very next request.
        time.sleep(1)
        run_analysis_pipeline(client, headers)

    print("Done. Sample data is seeded on engagement", ENGAGEMENT_ID)


if __name__ == "__main__":
    main()
