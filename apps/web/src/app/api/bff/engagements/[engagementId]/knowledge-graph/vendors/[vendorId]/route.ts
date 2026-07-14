import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { VendorNetwork } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; vendorId: string }>;
}

/** GET → the "vendor 360" view — a vendor plus every transaction paid to it. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, vendorId } = await context.params;
    const network = await apiFetch<VendorNetwork>(
      persona,
      `/v1/engagements/${engagementId}/knowledge-graph/vendors/${vendorId}`,
    );
    return NextResponse.json(network);
  });
}
