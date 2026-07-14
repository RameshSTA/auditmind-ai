import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Anomaly } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list anomalies the rule engine flagged for this engagement. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const anomalies = await apiFetch<Anomaly[]>(
      persona,
      `/v1/engagements/${engagementId}/anomalies`,
    );
    return NextResponse.json(anomalies);
  });
}
