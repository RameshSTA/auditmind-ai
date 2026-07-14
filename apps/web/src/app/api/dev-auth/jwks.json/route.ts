/**
 * Publishes the dev signing key's public half as a JWKS document.
 *
 * The FastAPI API is pointed at this URL in dev via AUDITMIND_ENTRA_JWKS_URI (see the increment
 * doc / docker-compose.dev-auth.yml). Its JWKSClient fetches and caches this exactly as it would
 * Entra's real https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys. Only reachable when
 * dev auth is switched on — served as a hard 404 otherwise so a non-dev build never advertises a
 * dev key.
 */
import { NextResponse } from "next/server";

import { DEV_JWKS } from "@/server/dev-auth";

export const dynamic = "force-dynamic";

export function GET() {
  if (process.env.AUDITMIND_DEV_AUTH !== "1") {
    return new NextResponse("Not found", { status: 404 });
  }
  return NextResponse.json(DEV_JWKS, {
    headers: { "cache-control": "no-store" },
  });
}
