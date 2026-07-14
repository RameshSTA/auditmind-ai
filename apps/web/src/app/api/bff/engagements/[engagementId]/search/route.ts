import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { SearchResult } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/**
 * GET ?q=&mode=keyword|semantic → search an engagement's evidence.
 * Routes to the API's keyword leg (/search) or vector leg (/search/semantic) by `mode`.
 */
export function GET(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const url = new URL(request.url);
    const q = url.searchParams.get("q") ?? "";
    const mode = url.searchParams.get("mode") === "semantic" ? "semantic" : "keyword";
    if (q.trim().length === 0) {
      return NextResponse.json([]);
    }
    const path =
      mode === "semantic"
        ? `/v1/engagements/${engagementId}/search/semantic`
        : `/v1/engagements/${engagementId}/search`;
    const results = await apiFetch<SearchResult[]>(persona, path, {
      searchParams: { q, limit: 20 },
    });
    return NextResponse.json(results);
  });
}
