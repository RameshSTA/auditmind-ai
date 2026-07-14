import { NextResponse } from "next/server";

import { agentApiFetch } from "@/server/agent-api-client";
import { withSession } from "@/server/bff";
import type { EvaluationMetrics } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → real aggregate analytics over this engagement's agent runs and HITL decisions. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const metrics = await agentApiFetch<EvaluationMetrics>(
      persona,
      `/v1/engagements/${engagementId}/evaluation/metrics`,
    );
    return NextResponse.json(metrics);
  });
}
