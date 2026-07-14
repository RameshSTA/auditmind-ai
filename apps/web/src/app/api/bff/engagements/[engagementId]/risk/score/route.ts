import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { RiskScore } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** POST → run the full fraud-scoring ensemble (rule engine, Isolation Forest, HDBSCAN cohort
 * clustering, graph centrality) and recompute every transaction's composite risk score. */
export function POST(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const scores = await apiFetch<RiskScore[]>(
      persona,
      `/v1/engagements/${engagementId}/risk/score`,
      { method: "POST" },
    );
    return NextResponse.json(scores, { status: 201 });
  });
}
