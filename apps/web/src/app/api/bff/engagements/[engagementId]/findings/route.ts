import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Finding } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list findings for an engagement. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const findings = await apiFetch<Finding[]>(
      persona,
      `/v1/engagements/${engagementId}/findings`,
    );
    return NextResponse.json(findings);
  });
}

/** POST → create a draft finding (API enforces the author-role RBAC and returns 403 otherwise). */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const body = (await request.json()) as unknown;
    const created = await apiFetch<Finding>(
      persona,
      `/v1/engagements/${engagementId}/findings`,
      { method: "POST", body },
    );
    return NextResponse.json(created, { status: 201 });
  });
}
