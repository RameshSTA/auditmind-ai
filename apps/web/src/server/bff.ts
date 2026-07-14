/**
 * BFF handler helpers shared by every /api/bff/* Route Handler.
 *
 * `withSession` resolves the persona from the httpOnly cookie (401 if none) and hands the handler
 * an authenticated context. `toResponse` translates an ApiError back into the same problem+json
 * shape and status the API produced, so the browser client sees the backend's real RFC 7807 error
 * (trace_id and all) rather than a generic 500 — the honest error surface Phase 13 §13 wants.
 */
import { NextResponse } from "next/server";

import { ApiError } from "@/server/api-client";
import type { DevPersona } from "@/server/dev-auth";
import { getSessionPersona } from "@/server/session";

export async function withSession(
  handler: (persona: DevPersona) => Promise<NextResponse>,
): Promise<NextResponse> {
  const persona = await getSessionPersona();
  if (!persona) {
    return NextResponse.json(
      { title: "Not signed in", status: 401, detail: "No active session." },
      { status: 401 },
    );
  }
  try {
    return await handler(persona);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        error.problem ?? { title: "API error", status: error.status, detail: error.message },
        { status: error.status },
      );
    }
    // Unexpected — surface as a 502 (the BFF couldn't reach or parse the API), never a fake 200.
    return NextResponse.json(
      {
        title: "Upstream unavailable",
        status: 502,
        detail: error instanceof Error ? error.message : "Unknown error contacting the API.",
      },
      { status: 502 },
    );
  }
}
