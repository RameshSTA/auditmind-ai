import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { InvestigationItem } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; investigationId: string }>;
}

/** GET → an investigation's working set of linked findings/anomalies/transactions. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, investigationId } = await context.params;
    const items = await apiFetch<InvestigationItem[]>(
      persona,
      `/v1/engagements/${engagementId}/investigations/${investigationId}/items`,
    );
    return NextResponse.json(items);
  });
}

/** POST → pull one finding, anomaly, or transaction into the investigation's working set. */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, investigationId } = await context.params;
    const body = (await request.json()) as unknown;
    const item = await apiFetch<InvestigationItem>(
      persona,
      `/v1/engagements/${engagementId}/investigations/${investigationId}/items`,
      { method: "POST", body },
    );
    return NextResponse.json(item, { status: 201 });
  });
}
