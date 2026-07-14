import { redirect } from "next/navigation";

import { getSessionPersona } from "@/server/session";
import { MonitoringPanel } from "@/components/MonitoringPanel";

export default async function MonitoringPage() {
  const persona = await getSessionPersona();
  if (!persona) redirect("/login");

  return (
    <div>
      <p className="kicker">System health</p>
      <h1 className="mt-1 mb-5">Monitoring</h1>
      <MonitoringPanel />
    </div>
  );
}
