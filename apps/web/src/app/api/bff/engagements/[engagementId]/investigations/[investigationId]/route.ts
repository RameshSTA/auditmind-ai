import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Investigation } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; investigationId: string }>;
}

/** GET → one investigation. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, investigationId } = await context.params;
    const investigation = await apiFetch<Investigation>(
      persona,
      `/v1/engagements/${engagementId}/investigations/${investigationId}`,
    );
    return NextResponse.json(investigation);
  });
}
