import { redirect } from "next/navigation";

import { getSessionPersona } from "@/server/session";
import { EngagementList } from "@/components/EngagementList";
import { PortfolioDashboard } from "@/components/PortfolioDashboard";

/** Home: the caller's engagement workspace(s) first — the actual thing there is to do here — then
 * a portfolio roll-up as supporting context below it, not ahead of it. Server-guards the session
 * first. */
export default async function EngagementsPage() {
  const persona = await getSessionPersona();
  if (!persona) redirect("/login");

  return (
    <div>
      <p className="kicker">
        {persona.displayName} · Portfolio
      </p>
      <h1 className="mt-1 mb-1">Your workspace</h1>
      <p className="lede mb-5">
        An engagement is the audit you&apos;re working — evidence, findings, risk &amp; fraud
        detection, reports, and everything else live inside one. Open yours below to get to work.
      </p>
      <EngagementList />

      <h2 className="mb-1 mt-10">Portfolio at a glance</h2>
      <p className="lede mb-4">
        A real-time roll-up across every engagement above — nothing here is cached or estimated.
      </p>
      <PortfolioDashboard />
    </div>
  );
}
