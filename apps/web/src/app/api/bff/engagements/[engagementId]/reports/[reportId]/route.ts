import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Report } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; reportId: string }>;
}

/** GET → a single report version's full record. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, reportId } = await context.params;
    const report = await apiFetch<Report>(
      persona,
      `/v1/engagements/${engagementId}/reports/${reportId}`,
    );
    return NextResponse.json(report);
  });
}
