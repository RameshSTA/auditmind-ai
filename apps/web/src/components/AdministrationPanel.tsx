"use client";

/**
 * Administration — the engagement roster (who has access, and what role). Read-only: there is no
 * membership-assignment (invite/add/remove) endpoint on the backend, so this doesn't pretend one
 * exists. `display_name`/`email` are whatever the identity context actually stored at JIT
 * provisioning — in this dev environment that's the Entra subject string (e.g. "dev-auditor"),
 * not a fabricated real-looking name (see identity/interface/dependencies.py's own comment on why).
 */
import { useQuery } from "@tanstack/react-query";

import { bffGet } from "@/lib/client-api";
import type { EngagementRosterEntry } from "@/lib/types";
import { ErrorNotice } from "@/components/ui";

export function AdministrationPanel({ engagementId }: { engagementId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["engagement-roster", engagementId],
    queryFn: () => bffGet<EngagementRosterEntry[]>(`/api/bff/engagements/${engagementId}/members`),
  });

  return (
    <section>
      <h2>Engagement roster</h2>
      <p className="lede mt-1">
        Everyone with access to this engagement and their role. Read-only — there is no
        membership-assignment feature yet (inviting or removing a member); this is who currently
        has access, not a place to change it.
      </p>

      {isLoading ? <p className="muted mt-4">Loading roster…</p> : null}
      {error ? <ErrorNotice error={error} /> : null}
      {data && data.length === 0 ? <p className="muted">No members found.</p> : null}
      {data && data.length > 0 ? (
        <table className="data">
          <thead>
            <tr>
              <th>Member</th>
              <th>Email</th>
              <th>Role</th>
              <th>Granted</th>
            </tr>
          </thead>
          <tbody>
            {data.map((entry) => (
              <tr key={entry.user_id}>
                <td>{entry.display_name}</td>
                <td className="mono">{entry.email || "—"}</td>
                <td>{entry.role}</td>
                <td className="muted">{new Date(entry.granted_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
