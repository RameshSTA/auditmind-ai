#!/usr/bin/env python3
"""Seed a demo engagement + persona members for the frontend dev environment.

DEV-ONLY. Idempotent: safe to run repeatedly. It seeds exactly the identities the frontend's
dev-auth personas claim (src/server/dev-auth.ts) so that when you "sign in" as a persona, the API's
JIT provisioning resolves the same identity.entra_object_id this script granted membership to, and
Row-Level Security lets that identity see this engagement's rows.

What it creates:
  - one tenant, one engagement (fixed ids, so the URL is stable across reseeds)
  - one identity.users row per persona, keyed by the persona's `subject` (the Entra object id)
  - one identity.credentials row per persona (a real bcrypt hash of DEMO_PASSWORD below), so the
    real email/password sign-in form has genuine demo accounts to authenticate against
  - engagement_members rows granting each persona its role — EXCEPT the "outsider" persona, which
    is deliberately left with no membership so the UI can demonstrate the 403 / empty-list path

It seeds ONLY the identity rows (tenant / engagement / users / memberships), on purpose. Documents,
findings, transactions, anomalies, and risk scores are all created THROUGH the running app/API in
the demo flow (upload a doc, import transactions, scan, draft/confirm a finding) — driving them
through the real endpoints is exactly what the UI exists to prove, and avoids this script having to
duplicate every table's NOT NULL/JSONB shape in raw SQL (which would rot the moment a migration
changes a column).

Run it against the migration/admin DB URL (the same one migrations use), never the app role:

  AUDITMIND_SEED_DATABASE_URL="postgresql://auditmind:auditmind_local_dev_only@localhost:5433/auditmind" \\
    python apps/web/scripts/seed_dev.py

Requires `psycopg` (or psycopg2) available; falls back to whichever is importable.
"""

from __future__ import annotations

import os
import sys

# Fixed ids so the engagement URL is stable and reseeding is a no-op, not a duplicate.
TENANT_ID = "00000000-0000-0000-0000-0000000000a1"
ENGAGEMENT_ID = "00000000-0000-0000-0000-0000000000e1"

# (subject / entra_object_id, display_name, engagement role or None for "no membership")
PERSONAS: list[tuple[str, str, str | None]] = [
    ("dev-auditor", "Raj Auditor", "Auditor"),
    ("dev-fraud-analyst", "Amara Analyst", "FraudAnalyst"),
    ("dev-compliance-manager", "Maria Manager", "ComplianceManager"),
    ("dev-cae", "Jonathan CAE", "CAE"),
    ("dev-outsider", "Sam Outsider", None),
]

# A real, bcrypt-hashed password seeded for every demo persona so the actual sign-in form (email +
# password, /login) has genuine accounts to authenticate against — not just the old click-to-pick
# list. This is the one password printed to stdout on purpose: it's a published demo credential,
# not a secret (Increment 14).
DEMO_PASSWORD = "AuditMind!Demo2026"

def _demo_password_hash() -> str:
    import bcrypt

    return bcrypt.hashpw(DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _connect(url: str):
    try:
        import psycopg  # type: ignore

        return psycopg.connect(url), "psycopg3"
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore

        return psycopg2.connect(url), "psycopg2"
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "Neither psycopg nor psycopg2 is installed. Install one, e.g. `pip install psycopg`, "
            "or run the equivalent INSERTs by hand (see this script's SQL)."
        ) from exc


def main() -> int:
    url = os.environ.get("AUDITMIND_SEED_DATABASE_URL")
    if not url:
        print(
            "Set AUDITMIND_SEED_DATABASE_URL to the admin/migration Postgres URL "
            "(e.g. postgresql://auditmind:...@localhost:5433/auditmind).",
            file=sys.stderr,
        )
        return 2

    conn, driver = _connect(url)
    print(f"Connected via {driver}.")
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO identity.tenants (id, name) VALUES (%s, 'Demo Tenant') "
                "ON CONFLICT (id) DO NOTHING",
                (TENANT_ID,),
            )
            # ON CONFLICT DO UPDATE (not DO NOTHING) for the name specifically: unlike the
            # tenant/persona rows above, this name is user-visible throughout the app (dashboard,
            # breadcrumb), so re-running this script after editing the literal below should always
            # bring an already-seeded environment's engagement name up to date, not freeze it at
            # whatever the first run happened to insert.
            cur.execute(
                "INSERT INTO identity.engagements (id, tenant_id, name) "
                "VALUES (%s, %s, 'FY2026 Q1 Accounts Payable Audit') "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                (ENGAGEMENT_ID, TENANT_ID),
            )

            for subject, display_name, role in PERSONAS:
                cur.execute(
                    "INSERT INTO identity.users (entra_object_id, display_name, email) "
                    "VALUES (%s, %s, %s) ON CONFLICT (entra_object_id) DO NOTHING",
                    (subject, display_name, f"{subject}@example.dev"),
                )
                cur.execute(
                    "SELECT id FROM identity.users WHERE entra_object_id = %s", (subject,)
                )
                row = cur.fetchone()
                assert row is not None
                user_id = row[0]

                cur.execute(
                    "INSERT INTO identity.credentials (user_id, password_hash) "
                    "VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, _demo_password_hash()),
                )

                if role is not None:
                    cur.execute(
                        "INSERT INTO identity.engagement_members (engagement_id, user_id, role) "
                        "VALUES (%s, %s, %s) ON CONFLICT (engagement_id, user_id) DO NOTHING",
                        (ENGAGEMENT_ID, user_id, role),
                    )
                    print(f"  member: {subject} → {role}")
                else:
                    print(f"  (no membership, by design): {subject}")

    print(f"\nDone. Demo engagement id: {ENGAGEMENT_ID}")
    print(f"Sign in at http://localhost:3000/login with any persona's email and password:")
    print(f"  password: {DEMO_PASSWORD}")
    for subject, _display_name, _role in PERSONAS:
        print(f"  {subject}@example.dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
