import { NextResponse } from "next/server";

import { clearSession } from "@/server/session";

export async function POST(request: Request) {
  await clearSession();
  return NextResponse.redirect(new URL("/", request.url), { status: 303 });
}
