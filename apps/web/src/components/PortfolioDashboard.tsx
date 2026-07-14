"use client";

/**
 * Portfolio dashboard — real aggregation across every engagement the caller belongs to, not a
 * mockup with invented numbers. Every figure here is a client-side reduction over data the same
 * BFF routes the Evidence/Findings/Risk panels already call — nothing new is fabricated to make
 * this screen look busier than the data actually is. An engagement with nothing in it yet
 * legitimately shows zeros, and the chart legitimately shows nothing until real findings exist.
 *
 * No agent-run evaluation metrics appear here on purpose: those are real, but per-engagement
 * (agent runs and HITL decisions are scoped to one engagement each) rather than a portfolio-wide
 * aggregate — see the Evaluation tab inside a given engagement instead.
 */
import { useQueries, useQuery } from "@tanstack/react-query";

import { bffGet } from "@/lib/client-api";
import type {
  Anomaly,
  DocumentSummary,
  Finding,
  FindingSeverity,
  Membership,
  RiskScore,
} from "@/lib/types";
import { ErrorNotice, Skeleton } from "@/components/ui";

const SEVERITIES: readonly FindingSeverity[] = ["critical", "high", "medium", "low"];
// Space-separated CSS Color 4 syntax via the theme's own custom properties (globals.css) — the
// same pattern FraudDetectionPanel.tsx's distribution chart uses for the identical bar-chart
// shape, rather than a second, hardcoded hex map that has to be kept in sync with it by hand.
const SEVERITY_BAR_COLOR: Record<FindingSeverity, string> = {
  critical: "rgb(var(--risk-critical))",
  high: "rgb(var(--risk-high))",
  medium: "rgb(var(--risk-medium))",
  low: "rgb(var(--risk-low))",
};

export function PortfolioDashboard() {
  const {
    data: memberships,
    isLoading: membershipsLoading,
    error: membershipsError,
  } = useQuery({
    queryKey: ["engagements"],
    queryFn: () => bffGet<Membership[]>("/api/bff/engagements"),
  });

  const engagementIds = (memberships ?? []).map((m) => m.engagement_id);

  const findingsQueries = useQueries({
    queries: engagementIds.map((id) => ({
      queryKey: ["dashboard", "findings", id],
      queryFn: () => bffGet<Finding[]>(`/api/bff/engagements/${id}/findings`),
    })),
  });
  const anomaliesQueries = useQueries({
    queries: engagementIds.map((id) => ({
      queryKey: ["dashboard", "anomalies", id],
      queryFn: () => bffGet<Anomaly[]>(`/api/bff/engagements/${id}/anomalies`),
    })),
  });
  const riskScoreQueries = useQueries({
    queries: engagementIds.map((id) => ({
      queryKey: ["dashboard", "risk-scores", id],
      queryFn: () => bffGet<RiskScore[]>(`/api/bff/engagements/${id}/risk-scores`),
    })),
  });
  const documentsQueries = useQueries({
    queries: engagementIds.map((id) => ({
      queryKey: ["dashboard", "documents", id],
      queryFn: () => bffGet<DocumentSummary[]>(`/api/bff/engagements/${id}/documents`),
    })),
  });

  if (membershipsLoading)
    return (
      <section className="mb-10">
        <div className="mb-8 flex flex-wrap gap-10">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="grid gap-1.5">
              <Skeleton className="h-9 w-16" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>
      </section>
    );
  if (membershipsError) return <ErrorNotice error={membershipsError} />;
  if (!memberships || memberships.length === 0) return null;

  const stillLoading = [
    ...findingsQueries,
    ...anomaliesQueries,
    ...riskScoreQueries,
    ...documentsQueries,
  ].some((q) => q.isLoading);

  const allFindings = findingsQueries.flatMap((q) => q.data ?? []);
  const allAnomalies = anomaliesQueries.flatMap((q) => q.data ?? []);
  const allRiskScores = riskScoreQueries.flatMap((q) => q.data ?? []);
  const allDocuments = documentsQueries.flatMap((q) => q.data ?? []);

  const findingsAwaitingSignOff = allFindings.filter((f) => f.status === "draft").length;
  const criticalOrHighOpen = allFindings.filter(
    (f) => f.status !== "rejected" && (f.severity === "critical" || f.severity === "high"),
  ).length;
  const openAnomalies = allAnomalies.filter((a) => a.status === "open").length;

  const severityCounts = SEVERITIES.map((severity) => ({
    severity,
    count: allFindings.filter((f) => f.severity === severity).length,
  }));
  const maxSeverityCount = Math.max(1, ...severityCounts.map((s) => s.count));

  return (
    <section className="mb-10">
      <div className="mb-8 flex flex-wrap gap-10">
        <Metric label="Engagements" value={memberships.length} loading={false} />
        <Metric label="Findings awaiting sign-off" value={findingsAwaitingSignOff} loading={stillLoading} />
        <Metric
          label="Critical & high risk findings"
          value={criticalOrHighOpen}
          loading={stillLoading}
          tone={criticalOrHighOpen > 0 ? "critical" : undefined}
        />
        <Metric label="Open anomalies" value={openAnomalies} loading={stillLoading} />
        <Metric label="Risk scores computed" value={allRiskScores.length} loading={stillLoading} />
        <Metric label="Documents processed" value={allDocuments.length} loading={stillLoading} />
      </div>

      {!stillLoading && allFindings.length > 0 ? (
        <div className="card max-w-md">
          <div className="mb-4 text-sm font-semibold">Findings by severity</div>
          <div className="flex h-20 items-end gap-3">
            {severityCounts.map(({ severity, count }) => (
              <div key={severity} className="flex flex-1 flex-col items-center gap-1.5">
                <div
                  className="w-full rounded-t"
                  style={{
                    height: `${Math.max(4, (count / maxSeverityCount) * 64)}px`,
                    background: SEVERITY_BAR_COLOR[severity],
                    opacity: count === 0 ? 0.15 : 1,
                  }}
                  title={`${count} ${severity}`}
                />
                <span className="mono text-[11px] text-ink-muted">{count}</span>
              </div>
            ))}
          </div>
          <div className="mt-2 flex justify-between">
            {severityCounts.map(({ severity }) => (
              <span key={severity} className="mono flex-1 text-center text-[11px] text-ink-muted">
                {severity}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Metric({
  label,
  value,
  tone,
  loading,
}: {
  label: string;
  value: number;
  tone?: "critical";
  loading: boolean;
}) {
  return (
    <div>
      <div className={`text-3xl font-bold ${tone === "critical" && value > 0 ? "text-risk-critical" : ""}`}>
        {loading ? "–" : value}
      </div>
      <div className="muted mt-0.5">{label}</div>
    </div>
  );
}
