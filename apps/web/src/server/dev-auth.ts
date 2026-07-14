/**
 * DEV-ONLY authentication stand-in.
 *
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 *  WHY THIS EXISTS AND WHAT IT IS NOT
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 * The real auth flow is OIDC Authorization Code + PKCE against a real Entra ID tenant, with this
 * Next.js app acting as a confidential client (a BFF). The browser holds only an httpOnly session
 * cookie; the BFF exchanges the code, holds the tokens server-side, and attaches a bearer token to
 * every call it forwards to the FastAPI API.
 *
 * No Entra tenant is available in this environment. Rather than build the UI against a fake
 * in-memory API, this module reproduces exactly the ONE property of Entra the FastAPI backend
 * actually validates against: it mints RS256 JWTs signed by a keypair whose public half is
 * published at a JWKS endpoint the API is pointed at. This is byte-for-byte
 * the same technique the backend's own test suite uses (apps/api/tests/conftest.py's `make_token`
 * + `rsa_keypair`) — a locally-generated RSA key standing in for an Entra signing key — lifted out
 * of the test suite and into the BFF so the real UI can drive the real API end-to-end.
 *
 * The keypair below is committed in cleartext ON PURPOSE. It is a throwaway dev credential, only
 * ever loaded when AUDITMIND_DEV_AUTH === "1", and it can sign tokens the API trusts ONLY because
 * the API is, in dev, explicitly configured to fetch its JWKS from this app. In any real
 * deployment AUDITMIND_DEV_AUTH is unset, the API points at Entra's real JWKS, and nothing this
 * file mints validates against anything. `assertDevAuthEnabled()` below is the guard that makes
 * that switch impossible to flip on by accident in a non-dev build.
 *
 * The "personas" are the same fixed roles the backend's `EngagementRole` enum defines. Choosing
 * a persona here === choosing which Entra identity the real login would have returned. The
 * engagement-level RBAC and Row-Level Security the API enforces are entirely
 * unchanged and untouched — this stand-in only answers "who is the caller," never "what may they
 * do," which stays 100% the backend's job.
 * ─────────────────────────────────────────────────────────────────────────────────────────────
 */
import { SignJWT, importPKCS8 } from "jose";

/** Dev issuer/audience — must match the API's AUDITMIND_ENTRA_ISSUER / AUDITMIND_ENTRA_CLIENT_ID. */
export const DEV_ISSUER = "https://login.microsoftonline.com/auditmind-dev-tenant/v2.0";
export const DEV_AUDIENCE = "auditmind-dev-api";
export const DEV_TENANT = "auditmind-dev-tenant";
export const DEV_KID = "auditmind-dev-key-1";

/**
 * Throwaway dev RSA private key (PKCS#8). Generated once for this increment; see the file header
 * for why committing it is safe. The matching public JWK is served from
 * `src/app/api/dev-auth/jwks.json/route.ts` and the API is pointed at that URL in dev.
 */
const DEV_PRIVATE_KEY_PKCS8 = `-----BEGIN PRIVATE KEY-----
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
-----END PRIVATE KEY-----`;

/** The public half, served verbatim at /api/dev-auth/jwks.json for the API to fetch. */
export const DEV_JWKS = {
  keys: [
    {
      kty: "RSA",
      use: "sig",
      alg: "RS256",
      kid: DEV_KID,
      n: "sihkONZDKZ0FZyPKRSR1_PrE38Ndegy2L5sMrc1-stvgrkNRc1Qy31pYP7ln9wkOEJllCT27pr_uzKyVphRKAZu8IUXlg5ipF4nPzSvT7Db6bfL82bBHYWGLRkMpK_qDUiHoU6w6UzrgHnDlnizxeFCxWtOVhIx0yFSUTsWk-HymC36_NCqsuw7-W4KAgDMdA6XRvXrSlcDZPk43PyefDdid8K3hiZNCkJri2qmFxR_8JnxTgiWaWcyJOr8BxLlyNy1PSSH8g60rDfeUdAsIwLdUa7F_nak8pywXf9gGgJACa2o7tFtMumdjiIo015KtCDbLpL8ZzAblCaC-6FInHQ",
      e: "AQAB",
    },
  ],
} as const;

/**
 * A dev persona — a fixed Entra identity the picker on the login screen offers, one per row of
 * the seeded fixture (see scripts/seed_dev.py). The `subject` here is the Entra object id the
 * seed script granted engagement membership to, so the
 * API's JIT provisioning + RLS resolve the same identity the persona claims to be.
 */
export interface DevPersona {
  readonly id: string;
  readonly label: string;
  readonly subject: string;
  readonly displayName: string;
  /** Coarse (tenant-wide) role claim carried in the JWT — NOT the per-engagement role, which the
   * API reads from identity.engagement_members, never from the token. */
  readonly tokenRoles: readonly string[];
  readonly note: string;
}

export const DEV_PERSONAS: readonly DevPersona[] = [
  {
    id: "auditor",
    label: "Raj Patel",
    subject: "dev-auditor",
    displayName: "Raj Patel",
    tokenRoles: ["Auditor"],
    note: "Senior Auditor — evidence review, control testing, and findings sign-off.",
  },
  {
    id: "fraud-analyst",
    label: "Amara Osei",
    subject: "dev-fraud-analyst",
    displayName: "Amara Osei",
    tokenRoles: ["FraudAnalyst"],
    note: "Fraud & Forensic Analyst — anomaly triage, investigations, and findings sign-off.",
  },
  {
    id: "compliance-manager",
    label: "David Chen",
    subject: "dev-compliance-manager",
    displayName: "David Chen",
    tokenRoles: ["ComplianceManager"],
    note: "Compliance & Controls Manager — drafts findings; sign-off requires an Auditor or Fraud Analyst.",
  },
  {
    id: "cae",
    label: "Jonathan Reyes",
    subject: "dev-cae",
    displayName: "Jonathan Reyes",
    tokenRoles: ["CAE"],
    note: "Chief Audit Executive — portfolio-wide, read-only oversight.",
  },
  {
    id: "outsider",
    label: "Sam Rivera",
    subject: "dev-outsider",
    displayName: "Sam Rivera",
    tokenRoles: ["Auditor"],
    note: "Not yet assigned to an engagement.",
  },
] as const;

export function findPersona(id: string): DevPersona | undefined {
  return DEV_PERSONAS.find((p) => p.id === id);
}

/** Hard guard: this module must never be reachable unless dev auth is explicitly switched on. */
export function assertDevAuthEnabled(): void {
  if (process.env.AUDITMIND_DEV_AUTH !== "1") {
    throw new Error(
      "Dev auth is disabled (AUDITMIND_DEV_AUTH !== '1'). Refusing to mint a dev token. " +
        "In a real deployment the OIDC/Entra BFF flow replaces this module entirely.",
    );
  }
}

/**
 * Mints a short-lived RS256 access token for a persona — the exact claim shape the API's
 * `decode_and_validate_token` checks (sub, aud, iss, exp, roles, tid).
 */
export async function mintDevToken(persona: DevPersona): Promise<string> {
  assertDevAuthEnabled();
  const privateKey = await importPKCS8(DEV_PRIVATE_KEY_PKCS8, "RS256");
  return new SignJWT({ roles: [...persona.tokenRoles], tid: DEV_TENANT })
    .setProtectedHeader({ alg: "RS256", kid: DEV_KID })
    .setSubject(persona.subject)
    .setAudience(DEV_AUDIENCE)
    .setIssuer(DEV_ISSUER)
    .setIssuedAt()
    .setExpirationTime("8h")
    .sign(privateKey);
}
