import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** POST → resolve vendor entities from the engagement's current transactions into the knowledge
 * graph (Increment 09). Idempotent and self-healing — safe to call again after importing more
 * transactions. */
export function POST(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const result = await apiFetch<{ newly_resolved_count: number }>(
      persona,
      `/v1/engagements/${engagementId}/knowledge-graph/resolve`,
      { method: "POST" },
    );
    return NextResponse.json(result);
  });
}
