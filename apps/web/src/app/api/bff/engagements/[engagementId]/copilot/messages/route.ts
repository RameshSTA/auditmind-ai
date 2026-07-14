import { NextResponse } from "next/server";

import { agentApiFetch } from "@/server/agent-api-client";
import { withSession } from "@/server/bff";
import type { CopilotMessage } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → the calling user's own private AI Copilot thread for this engagement. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const messages = await agentApiFetch<CopilotMessage[]>(
      persona,
      `/v1/engagements/${engagementId}/copilot/messages`,
    );
    return NextResponse.json(messages);
  });
}

/** POST → send one chat turn. agent-orchestrator's router decides to answer directly, invoke a
 * direct action, or hand off to a real investigation — this route is a plain proxy either way. */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const body = (await request.json()) as unknown;
    const messages = await agentApiFetch<CopilotMessage[]>(
      persona,
      `/v1/engagements/${engagementId}/copilot/messages`,
      { method: "POST", body },
    );
    return NextResponse.json(messages, { status: 201 });
  });
}
