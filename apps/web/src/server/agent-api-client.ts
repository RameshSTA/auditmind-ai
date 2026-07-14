/**
 * Server-side client for the agent-orchestrator service — the second backend in this stack
 * (docker-compose's `agent-orchestrator`, port 8001, distinct from apps/api's port 8000). Mirrors
 * server/api-client.ts's shape exactly; kept as a separate module rather than parameterizing
 * apiFetch with a base URL because the two backends are genuinely different deployables with
 * independent lifecycles (Phase 5's ADR-001) — conflating them behind one client would blur that.
 *
 * Reuses the same dev bearer token apps/api accepts: agent-orchestrator validates against the
 * identical platform-wide Entra tenant (see server/dev-auth.ts's header), so one mint serves both.
 */
import { mintDevToken, type DevPersona } from "@/server/dev-auth";
import { ApiError, type ProblemDetail } from "@/server/api-client";

const AGENT_API_BASE_URL = process.env.AUDITMIND_AGENT_API_BASE_URL ?? "http://localhost:8001";

interface AgentApiRequestOptions {
  method?: string;
  body?: unknown;
}

export async function agentApiFetch<T>(
  persona: DevPersona,
  path: string,
  options: AgentApiRequestOptions = {},
): Promise<T> {
  const token = await mintDevToken(persona);
  const url = new URL(path, AGENT_API_BASE_URL);

  const headers: Record<string, string> = { authorization: `Bearer ${token}` };
  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body,
    cache: "no-store",
  });

  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      problem = null;
    }
    throw new ApiError(
      response.status,
      problem,
      problem?.detail ?? `Agent API request failed with status ${response.status}`,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
