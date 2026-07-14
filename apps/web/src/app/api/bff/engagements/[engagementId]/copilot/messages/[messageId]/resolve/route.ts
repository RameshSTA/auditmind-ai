import { NextResponse } from "next/server";

import { agentApiFetch } from "@/server/agent-api-client";
import { withSession } from "@/server/bff";
import type { CopilotMessage } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string; messageId: string }>;
}

/** POST → resolve the HITL checkpoint an earlier chat message handed off to (approve/reject/edit)
 * — the same governance act the standalone agent-runs resolve endpoint performs, reached from the
 * chat thread by message id alone, with no separate run/interrupt id for the UI to track. */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, messageId } = await context.params;
    const body = (await request.json()) as unknown;
    const message = await agentApiFetch<CopilotMessage>(
      persona,
      `/v1/engagements/${engagementId}/copilot/messages/${messageId}/resolve`,
      { method: "POST", body },
    );
    return NextResponse.json(message);
  });
}
