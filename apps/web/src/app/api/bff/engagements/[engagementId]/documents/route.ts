import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { DocumentSummary } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list an engagement's documents. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const documents = await apiFetch<DocumentSummary[]>(
      persona,
      `/v1/engagements/${engagementId}/documents`,
    );
    return NextResponse.json(documents);
  });
}

/** POST → upload a document (multipart) into the engagement's evidence store. */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const incoming = await request.formData();
    const file = incoming.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json(
        { title: "No file", status: 400, detail: "A 'file' field is required." },
        { status: 400 },
      );
    }
    const outgoing = new FormData();
    outgoing.append("file", file, file.name);
    const created = await apiFetch<unknown>(
      persona,
      `/v1/engagements/${engagementId}/documents`,
      { method: "POST", formData: outgoing },
    );
    return NextResponse.json(created, { status: 201 });
  });
}
