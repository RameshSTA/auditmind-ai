import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Investigation } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; investigationId: string }>;
}

/**
 * POST → close an investigation with a documented conclusion. The API restricts this to
 * Auditor / Fraud Analyst and 403s everyone else — the UI does not pre-judge, it shows the
 * button and surfaces the API's decision, the same discipline finding confirm/reject follows.
 */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, investigationId } = await context.params;
    const body = (await request.json()) as unknown;
    const investigation = await apiFetch<Investigation>(
      persona,
      `/v1/engagements/${engagementId}/investigations/${investigationId}/close`,
      { method: "POST", body },
    );
    return NextResponse.json(investigation);
  });
}
