import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Anomaly } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** POST → run the rule engine (Benford's Law, duplicate-payment matching, threshold/round-dollar
 * detection) over the engagement's current transactions. Returns only newly-created anomalies. */
export function POST(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const anomalies = await apiFetch<Anomaly[]>(
      persona,
      `/v1/engagements/${engagementId}/risk/scan`,
      { method: "POST", body: {} },
    );
    return NextResponse.json(anomalies, { status: 201 });
  });
}
