import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";

interface RouteContext {
  params: Promise<{ engagementId: string; documentId: string }>;
}

/** POST → generate and store BGE-M3 embeddings for every chunk of a document, so semantic search
 * has something to search. Idempotent: already-embedded chunks are skipped, not re-embedded. */
export function POST(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, documentId } = await context.params;
    const result = await apiFetch<{ document_id: string; embedded_count: number }>(
      persona,
      `/v1/engagements/${engagementId}/documents/${documentId}/embeddings`,
      { method: "POST" },
    );
    return NextResponse.json(result, { status: 201 });
  });
}
