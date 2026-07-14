import { NextResponse } from "next/server";

import { apiFetchBinary } from "@/server/api-client";
import { withSession } from "@/server/bff";

interface RouteContext {
  params: Promise<{ engagementId: string; reportId: string }>;
}

/** GET → the report's PDF, generated on first request and served from cache on every request
 * after (see apps/api's `get_or_export_report_pdf`). Streams the upstream response straight
 * through rather than buffering it into a JS string — the one BFF route whose body isn't JSON. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId, reportId } = await context.params;
    const upstream = await apiFetchBinary(
      persona,
      `/v1/engagements/${engagementId}/reports/${reportId}/pdf`,
    );
    return new NextResponse(upstream.body, {
      status: 200,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/pdf",
        "content-disposition": upstream.headers.get("content-disposition") ?? "attachment",
      },
    });
  });
}
