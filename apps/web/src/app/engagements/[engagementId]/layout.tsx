import type { ReactNode } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";

import { apiFetch } from "@/server/api-client";
import { getSessionPersona } from "@/server/session";

/**
 * Engagement shell: just the breadcrumb. Evidence / Findings / Risk navigation lives in the
 * sidebar's "Workspace" group now (Sidebar.tsx) — an in-page tab bar here would duplicate it.
 * Everything under an engagement is scoped to that one engagement.
 */
export default async function EngagementLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ engagementId: string }>;
}) {
  const persona = await getSessionPersona();
  if (!persona) redirect("/login");
  const { engagementId } = await params;

  // A server component can call the API directly (no BFF round-trip needed for a page-shell
  // breadcrumb) — falls back to the bare id only if the lookup itself fails, never a placeholder.
  const engagement = await apiFetch<{ name: string }>(
    persona,
    `/v1/engagements/${engagementId}`,
  ).catch(() => null);

  return (
    <div>
      <p className="kicker">
        <Link href="/engagements">Engagements</Link> /{" "}
        {engagement ? engagement.name : `${engagementId.slice(0, 8)}…`}
      </p>
      {children}
    </div>
  );
}
