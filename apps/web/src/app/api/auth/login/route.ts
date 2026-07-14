/**
 * Sign in: verifies email/password against the API's `identity.credentials` table and, on
 * success, records the resolved identity in the session cookie — the real counterpart to the old
 * account-picker this replaces. The API never issues a token itself (see `dev-auth.ts`'s module
 * docstring); this route is the one place that turns "who the API says this is" into a session a
 * later request can mint a bearer token for.
 */
import { NextResponse } from "next/server";

import { ApiError, publicApiFetch } from "@/server/api-client";
import type { AuthIdentity } from "@/lib/types";
import { setSessionPersona } from "@/server/session";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  if (!body || typeof body.email !== "string" || typeof body.password !== "string") {
    return NextResponse.json(
      { title: "Invalid request", status: 400, detail: "email and password are required." },
      { status: 400 },
    );
  }

  try {
    const identity = await publicApiFetch<AuthIdentity>("/v1/auth/login", {
      email: body.email,
      password: body.password,
    });
    await setSessionPersona({
      id: identity.subject,
      label: identity.display_name,
      subject: identity.subject,
      displayName: identity.display_name,
      tokenRoles: identity.role ? [identity.role] : [],
      note: identity.role ? `${identity.role} — ${identity.email}` : identity.email,
    });
    return NextResponse.json({ ok: true });
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        error.problem ?? { title: "Sign in failed", status: error.status, detail: error.message },
        { status: error.status },
      );
    }
    return NextResponse.json(
      { title: "Upstream unavailable", status: 502, detail: "Could not reach the API." },
      { status: 502 },
    );
  }
}
