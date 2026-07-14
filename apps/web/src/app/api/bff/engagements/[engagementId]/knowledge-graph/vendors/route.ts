import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Vendor } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list every vendor resolved into the knowledge graph for this engagement, with aggregate
 * transaction stats. The graph (Neo4j) is this context's read model, not a Postgres table. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const vendors = await apiFetch<Vendor[]>(
      persona,
      `/v1/engagements/${engagementId}/knowledge-graph/vendors`,
    );
    return NextResponse.json(vendors);
  });
}
