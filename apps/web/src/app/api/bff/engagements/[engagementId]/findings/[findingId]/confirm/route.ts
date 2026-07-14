import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Finding } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; findingId: string }>;
}

/**
 * POST → confirm a finding through the mandatory human sign-off gate. The API restricts this to
 * Auditor / Fraud Analyst (Phase 11 §2) and 403s everyone else — the UI does not pre-judge, it
 * shows the button and surfaces the API's decision, so the gate is enforced in exactly one place.
 */
export function POST(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, findingId } = await context.params;
    const finding = await apiFetch<Finding>(
      persona,
      `/v1/engagements/${engagementId}/findings/${findingId}/confirm`,
      { method: "POST" },
    );
    return NextResponse.json(finding);
  });
}
