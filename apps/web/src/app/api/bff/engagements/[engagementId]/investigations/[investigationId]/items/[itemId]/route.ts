import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";

interface RouteContext {
  params: Promise<{ engagementId: string; investigationId: string; itemId: string }>;
}

/** DELETE → remove one item from the investigation's working set. */
export function DELETE(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, investigationId, itemId } = await context.params;
    await apiFetch<void>(
      persona,
      `/v1/engagements/${engagementId}/investigations/${investigationId}/items/${itemId}`,
      { method: "DELETE" },
    );
    return new NextResponse(null, { status: 204 });
  });
}
