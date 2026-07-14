import { NextResponse } from "next/server";

import { getSessionPersona } from "@/server/session";

const API_BASE_URL = process.env.AUDITMIND_API_BASE_URL ?? "http://localhost:8000";
const AGENT_API_BASE_URL = process.env.AUDITMIND_AGENT_API_BASE_URL ?? "http://localhost:8001";

interface ServiceHealth {
  name: string;
  healthy: boolean;
  ready: boolean;
  error: string | null;
}

async function checkService(name: string, baseUrl: string): Promise<ServiceHealth> {
  try {
    const [healthzRes, readyzRes] = await Promise.all([
      fetch(new URL("/healthz", baseUrl), { cache: "no-store" }),
      fetch(new URL("/readyz", baseUrl), { cache: "no-store" }),
    ]);
    return { name, healthy: healthzRes.ok, ready: readyzRes.ok, error: null };
  } catch (err) {
    return {
      name,
      healthy: false,
      ready: false,
      error: err instanceof Error ? err.message : "Unreachable",
    };
  }
}

/** GET → real liveness/readiness of both backend services, checked live on every call — never a
 * cached or assumed status. Requires a session (this is operational information, not public), but
 * unlike every other BFF route it doesn't call apiFetch/agentApiFetch: /healthz and /readyz are
 * intentionally unauthenticated on both services (Phase 12 §13), so no bearer token is minted. */
export async function GET() {
  const persona = await getSessionPersona();
  if (!persona) {
    return NextResponse.json(
      { title: "Not signed in", status: 401, detail: "No active session." },
      { status: 401 },
    );
  }

  const [api, agentOrchestrator] = await Promise.all([
    checkService("api", API_BASE_URL),
    checkService("agent-orchestrator", AGENT_API_BASE_URL),
  ]);

  return NextResponse.json({ services: [api, agentOrchestrator] });
}
