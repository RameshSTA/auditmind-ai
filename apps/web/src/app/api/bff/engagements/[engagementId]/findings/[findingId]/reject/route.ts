import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Finding } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; findingId: string }>;
}

/** POST → reject a finding with a documented reason (US-06). The API requires a non-empty reason. */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, findingId } = await context.params;
    const body = (await request.json()) as unknown;
    const finding = await apiFetch<Finding>(
      persona,
      `/v1/engagements/${engagementId}/findings/${findingId}/reject`,
      { method: "POST", body },
    );
    return NextResponse.json(finding);
  });
}
