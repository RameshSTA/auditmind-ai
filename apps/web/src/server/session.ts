/**
 * Server-side session.
 *
 * The browser holds ONLY an httpOnly, SameSite cookie; it never holds a bearer token. In the
 * real design the cookie keys into a server-side session store
 * holding Entra tokens. In this dev stand-in the cookie holds the resolved identity's claims
 * (subject, display name, roles) as JSON — never a token, and never readable by client JS (it's
 * httpOnly) — the bearer token is still minted fresh, server-side, per outbound API call (see
 * `api-client.ts`). Swapping this dev stand-in for the real OIDC BFF later is a change confined to
 * this file and `dev-auth.ts`; nothing in the UI reads the cookie.
 *
 * Storing the identity itself (not just a lookup key into a fixed persona list) is what lets this
 * same session mechanism serve both the seeded demo accounts and real self-service signups: a
 * freshly-registered user isn't in any fixed list to look up by id.
 */
import { cookies } from "next/headers";

import type { DevPersona } from "@/server/dev-auth";

const SESSION_COOKIE = "auditmind_session";

export async function getSessionPersona(): Promise<DevPersona | null> {
  const store = await cookies();
  const value = store.get(SESSION_COOKIE)?.value;
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<DevPersona>;
    if (!parsed.subject || !parsed.displayName || !Array.isArray(parsed.tokenRoles)) return null;
    return parsed as DevPersona;
  } catch {
    return null;
  }
}

export async function setSessionPersona(persona: DevPersona): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, JSON.stringify(persona), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
}
