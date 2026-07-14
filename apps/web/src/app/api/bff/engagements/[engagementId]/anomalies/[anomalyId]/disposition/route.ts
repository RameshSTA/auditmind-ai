import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Anomaly } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; anomalyId: string }>;
}

/**
 * POST → disposition an anomaly (confirm/dismiss) through the AC-03 human-review gate. Restricted
 * by the API to Auditor / Fraud Analyst, the same HITL roles as finding sign-off.
 */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, anomalyId } = await context.params;
    const body = (await request.json()) as unknown;
    const anomaly = await apiFetch<Anomaly>(
      persona,
      `/v1/engagements/${engagementId}/anomalies/${anomalyId}/disposition`,
      { method: "POST", body },
    );
    return NextResponse.json(anomaly);
  });
}
