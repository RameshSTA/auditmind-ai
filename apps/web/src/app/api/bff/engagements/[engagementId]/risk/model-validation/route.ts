import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { ModelValidationResult } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → cross-validated Isolation Forest / HDBSCAN metrics and combiner ablation, computed live
 * over the engagement's real transactions/anomalies/risk scores. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const result = await apiFetch<ModelValidationResult>(
      persona,
      `/v1/engagements/${engagementId}/risk/model-validation`,
    );
    return NextResponse.json(result);
  });
}
