import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Membership } from "@/lib/types";

/** GET /api/bff/engagements → the caller's engagement memberships (RLS-scoped by the API). */
export function GET() {
  return withSession(async (persona) => {
    const memberships = await apiFetch<Membership[]>(persona, "/v1/me/engagements");
    return NextResponse.json(memberships);
  });
}
