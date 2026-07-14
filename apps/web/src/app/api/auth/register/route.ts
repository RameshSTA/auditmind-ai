/**
 * Self-service signup: creates a real account on the API (identity.users + identity.credentials +
 * engagement membership with the chosen role), then mints this environment's session for it — the
 * same handoff `login`'s route makes, just arriving from account creation instead of an existing
 * one. See `register/page` (the `/login?intent=create` form) for the client side of this call.
 */
import { NextResponse } from "next/server";

import { ApiError, publicApiFetch } from "@/server/api-client";
import type { AuthIdentity } from "@/lib/types";
import { setSessionPersona } from "@/server/session";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  if (
    !body ||
    typeof body.email !== "string" ||
    typeof body.password !== "string" ||
    typeof body.display_name !== "string" ||
    typeof body.role !== "string"
  ) {
    return NextResponse.json(
      { title: "Invalid request", status: 400, detail: "email, password, display_name, and role are required." },
      { status: 400 },
    );
  }

  try {
    const identity = await publicApiFetch<AuthIdentity>("/v1/auth/register", {
      email: body.email,
      password: body.password,
      display_name: body.display_name,
      role: body.role,
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
        error.problem ?? { title: "Registration failed", status: error.status, detail: error.message },
        { status: error.status },
      );
    }
    return NextResponse.json(
      { title: "Upstream unavailable", status: 502, detail: "Could not reach the API." },
      { status: 502 },
    );
  }
}
