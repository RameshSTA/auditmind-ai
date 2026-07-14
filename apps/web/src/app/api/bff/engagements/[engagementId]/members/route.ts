import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { EngagementRosterEntry } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → every member of this engagement and their role — Administration's roster view. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const roster = await apiFetch<EngagementRosterEntry[]>(
      persona,
      `/v1/engagements/${engagementId}/members`,
    );
    return NextResponse.json(roster);
  });
}
