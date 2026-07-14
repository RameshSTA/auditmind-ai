import { redirect } from "next/navigation";

import { LoginForm } from "@/components/LoginForm";
import { DEV_PERSONAS } from "@/server/dev-auth";
import { getSessionPersona } from "@/server/session";

/**
 * Sign in / create account — a real identity flow, not a "pick who you are" list. Signing in
 * verifies email + password against `identity.credentials` (real bcrypt hashes); creating an
 * account writes a real `identity.users` + `identity.credentials` row and auto-joins the demo
 * engagement with the role chosen below. See `api/auth/login` and `api/auth/register` for the
 * server side of both calls.
 *
 * The seeded demo personas (scripts/seed_dev.py) are still reachable — through this same real
 * form, with a real (published) password — not a separate shortcut mechanism.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ intent?: string }>;
}) {
  const existing = await getSessionPersona();
  if (existing) redirect("/engagements");
  const { intent } = await searchParams;

  const demoEmails = DEV_PERSONAS.filter((p) => p.id !== "outsider").map(
    (p) => `${p.subject}@example.dev`,
  );

  return (
    <LoginForm
      initialMode={intent === "create" ? "create" : "signin"}
      demoEmails={demoEmails}
      demoPassword="AuditMind!Demo2026"
    />
  );
}
