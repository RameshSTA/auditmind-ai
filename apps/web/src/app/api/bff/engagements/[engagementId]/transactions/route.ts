import { NextResponse } from "next/server";

import { apiFetch } from "@/server/api-client";
import { withSession } from "@/server/bff";
import type { TransactionRecord } from "@/lib/types";

interface RouteContext {
  params: Promise<{ engagementId: string }>;
}

/** GET → list an engagement's imported transactions. */
export function GET(_request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const transactions = await apiFetch<TransactionRecord[]>(
      persona,
      `/v1/engagements/${engagementId}/transactions`,
    );
    return NextResponse.json(transactions);
  });
}

/** POST → bulk-import transaction records (the only path transaction data enters the platform
 * until a real ERP connector exists). */
export function POST(request: Request, context: RouteContext) {
  return withSession(async (persona) => {
    const { engagementId } = await context.params;
    const body = (await request.json()) as unknown;
    const created = await apiFetch<TransactionRecord[]>(
      persona,
      `/v1/engagements/${engagementId}/transactions`,
      { method: "POST", body },
    );
    return NextResponse.json(created, { status: 201 });
  });
}
