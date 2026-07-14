import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { RiskScore } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list computed risk scores for the engagement's transactions. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const scores = await apiFetch<RiskScore[]>(
      persona,
      `/v1/engagements/${engagementId}/risk-scores`,
    );
    return NextResponse.json(scores);
  });
}
