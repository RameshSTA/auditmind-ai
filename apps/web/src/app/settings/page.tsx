import { redirect } from "next/navigation";

import { getSessionPersona } from "@/server/session";
import { SettingsPanel } from "@/components/SettingsPanel";

export default async function SettingsPage() {
  const persona = await getSessionPersona();
  if (!persona) redirect("/login");

  return (
    <div>
      <p className="kicker">Preferences</p>
      <h1 className="mt-1 mb-5">Settings</h1>
      <SettingsPanel />
    </div>
  );
}
