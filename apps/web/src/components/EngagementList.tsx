"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { bffGet } from "@/lib/client-api";
import type { Membership } from "@/lib/types";
import { ErrorNotice, SkeletonCards } from "@/components/ui";

/**
 * The one real navigational decision this whole page exists to make obvious: which engagement to
 * open. Previously a bare `.card` with three lines of plain text and no visual cue that it was
 * the only door into Findings/Risk/Reports/etc. — every card here now states what's inside in
 * words and ends in an explicit "Open workspace" affordance, not an implicit whole-card link a
 * reader has to discover by hovering.
 */
export function EngagementList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["engagements"],
    queryFn: () => bffGet<Membership[]>("/api/bff/engagements"),
  });

  if (isLoading) return <SkeletonCards count={1} />;
  if (error) return <ErrorNotice error={error} />;
  if (!data || data.length === 0) {
    return (
      <div className="notice warn">
        You&apos;re not yet assigned to an engagement. Ask an engagement administrator to add you,
        or contact your account team.
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      {data.map((m) => (
        <Link key={m.engagement_id} href={`/engagements/${m.engagement_id}`} className="workspace-card">
          <div>
            <div className="kicker">Engagement · Your role: {m.role}</div>
            <div className="mt-1 text-lg font-semibold text-ink">{m.name}</div>
            <p className="muted mt-1">
              Evidence, findings, risk &amp; fraud detection, reports, and AI Copilot for this
              engagement.
            </p>
          </div>
          <span className="workspace-card-cta">
            Open workspace
            <ArrowRight size={15} strokeWidth={2} aria-hidden="true" />
          </span>
        </Link>
      ))}
    </div>
  );
}
