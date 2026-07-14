import { NextResponse } from "next/server";

import { getSessionPersona } from "@/server/session";

/** Returns the current persona (who am I) for the client shell — never a token, just identity. */
export async function GET() {
  const persona = await getSessionPersona();
  if (!persona) {
    return NextResponse.json({ authenticated: false }, { status: 200 });
  }
  return NextResponse.json({
    authenticated: true,
    id: persona.id,
    label: persona.label,
    displayName: persona.displayName,
    tokenRoles: persona.tokenRoles,
    note: persona.note,
  });
}
