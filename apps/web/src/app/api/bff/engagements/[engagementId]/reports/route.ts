import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Report } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list every report version generated for this engagement. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const reports = await apiFetch<Report[]>(persona, `/v1/engagements/${engagementId}/reports`);
    return NextResponse.json(reports);
  });
}

/** POST → compile every currently-confirmed finding into a new versioned report. Document export
 * (PDF/DOCX) is deferred backend-side — exported_uri is always null for now. */
export function POST(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const report = await apiFetch<Report>(
      persona,
      `/v1/engagements/${engagementId}/reports`,
      { method: "POST" },
    );
    return NextResponse.json(report, { status: 201 });
  });
}
