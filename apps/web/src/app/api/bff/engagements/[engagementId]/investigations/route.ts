import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { Investigation } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list investigations for an engagement. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const investigations = await apiFetch<Investigation[]>(
      persona,
      `/v1/engagements/${engagementId}/investigations`,
    );
    return NextResponse.json(investigations);
  });
}

/** POST → open a new investigation (API enforces the author-role RBAC and returns 403 otherwise). */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const body = (await request.json()) as unknown;
    const created = await apiFetch<Investigation>(
      persona,
      `/v1/engagements/${engagementId}/investigations`,
      { method: "POST", body },
    );
    return NextResponse.json(created, { status: 201 });
  });
}
