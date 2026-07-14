"use client";

/**
 * Fraud Detection — a risk-sorted transaction triage view. Everything here is a client-side join
 * over data three endpoints Risk & Anomalies already fetches (transactions, risk-scores,
 * anomalies) — RiskScore.subject_id matches Transaction.id when subject_type is "transaction"
 * (main.py's own response builders confirm this), so no new backend endpoint was needed. This is
 * deliberately NOT a duplicate of Risk & Anomalies: that page is where you import data and act on
 * individual anomalies; this page answers "which transactions should I look at first," sorted by
 * composite risk score, highest first — the actual point of a fraud-triage screen.
 */
import { useQuery } from "@tanstack/react-query";

import { bffGet } from "@/lib/client-api";
import type { Anomaly, FindingSeverity, RiskScore, TransactionRecord } from "@/lib/types";
import { ErrorNotice, SeverityChip, SkeletonTable } from "@/components/ui";

interface TriageRow {
  transaction: TransactionRecord;
  score: RiskScore | null;
  openAnomalyCount: number;
}

function severityBand(score: number): FindingSeverity {
  if (score >= 75) return "critical";
  if (score >= 50) return "high";
  if (score >= 25) return "medium";
  return "low";
}

const BAND_ORDER: readonly FindingSeverity[] = ["critical", "high", "medium", "low"];

// Space-separated CSS Color 4 syntax (`rgb(217 48 37)`, not `rgb(217, 48, 37)`) — substituting a
// custom property holding "R G B" (globals.css) into `rgb(var(--x))` produces exactly that, and
// picks up the current theme's value automatically (no separate dark-mode map needed here, unlike
// PortfolioDashboard.tsx's older hardcoded-hex version of this same bar-chart pattern).
const BAND_COLOR: Record<FindingSeverity, string> = {
  critical: "rgb(var(--risk-critical))",
  high: "rgb(var(--risk-high))",
  medium: "rgb(var(--risk-medium))",
  low: "rgb(var(--risk-low))",
};

export function FraudDetectionPanel({ engagementId }: { engagementId: string }) {
  const { data: transactions, isLoading: txLoading, error: txError } = useQuery({
    queryKey: ["transactions", engagementId],
    queryFn: () => bffGet<TransactionRecord[]>(`/api/bff/engagements/${engagementId}/transactions`),
  });
  const { data: scores, isLoading: scoresLoading, error: scoresError } = useQuery({
    queryKey: ["risk-scores", engagementId],
    queryFn: () => bffGet<RiskScore[]>(`/api/bff/engagements/${engagementId}/risk-scores`),
  });
  const { data: anomalies, isLoading: anomaliesLoading, error: anomaliesError } = useQuery({
    queryKey: ["anomalies", engagementId],
    queryFn: () => bffGet<Anomaly[]>(`/api/bff/engagements/${engagementId}/anomalies`),
  });

  const isLoading = txLoading || scoresLoading || anomaliesLoading;
  const error = txError ?? scoresError ?? anomaliesError;

  if (isLoading)
    return (
      <div className="panel-stack">
        <section>
          <h2>Fraud Detection</h2>
          <SkeletonTable rows={4} cols={5} />
        </section>
      </div>
    );
  if (error) return <ErrorNotice error={error} />;
  if (!transactions) return null;

  const scoresByTransactionId = new Map<string, RiskScore>();
  for (const score of scores ?? []) {
    if (score.subject_type === "transaction") scoresByTransactionId.set(score.subject_id, score);
  }
  const openAnomalyCountByTransactionId = new Map<string, number>();
  for (const anomaly of anomalies ?? []) {
    if (anomaly.status !== "open" || !anomaly.transaction_id) continue;
    openAnomalyCountByTransactionId.set(
      anomaly.transaction_id,
      (openAnomalyCountByTransactionId.get(anomaly.transaction_id) ?? 0) + 1,
    );
  }

  const rows: TriageRow[] = transactions
    .map((transaction) => ({
      transaction,
      score: scoresByTransactionId.get(transaction.id) ?? null,
      openAnomalyCount: openAnomalyCountByTransactionId.get(transaction.id) ?? 0,
    }))
    .sort((a, b) => {
      const scoreA = a.score ? Number(a.score.score) : -1;
      const scoreB = b.score ? Number(b.score.score) : -1;
      return scoreB - scoreA;
    });

  const bandCounts: Record<FindingSeverity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const row of rows) {
    if (row.score) bandCounts[severityBand(Number(row.score.score))] += 1;
  }
  const maxBandCount = Math.max(1, ...BAND_ORDER.map((b) => bandCounts[b]));
  const scoredCount = rows.filter((r) => r.score !== null).length;

  return (
    <div className="panel-stack">
      <section>
        <h2>Fraud Detection</h2>
        <p className="lede mt-1">
          Every transaction with a computed risk score, sorted highest-risk first — the triage
          order for an investigation. Import transactions and compute scores on the Risk &amp;
          Anomalies tab; this view reads the same data, joined and ranked. Transactions without a
          score yet (below the ML minimum sample size, or scores not computed) sort to the bottom.
        </p>

        {transactions.length === 0 ? (
          <p className="muted mt-4">
            No transactions imported yet. Import some on the Risk &amp; Anomalies tab first.
          </p>
        ) : null}

        {scoredCount > 0 ? (
          <div className="card mt-4 max-w-md">
            <div className="mb-4 text-sm font-semibold">Risk distribution ({scoredCount} scored)</div>
            <div className="flex h-20 items-end gap-3">
              {BAND_ORDER.map((band) => (
                <div key={band} className="flex flex-1 flex-col items-center gap-1.5">
                  <div
                    className="w-full rounded-t"
                    style={{
                      height: `${Math.max(4, (bandCounts[band] / maxBandCount) * 64)}px`,
                      background: BAND_COLOR[band],
                      opacity: bandCounts[band] === 0 ? 0.15 : 1,
                    }}
                    title={`${bandCounts[band]} ${band}`}
                  />
                  <span className="mono text-[11px] text-ink-muted">{bandCounts[band]}</span>
                </div>
              ))}
            </div>
            <div className="mt-2 flex justify-between">
              {BAND_ORDER.map((band) => (
                <span key={band} className="mono flex-1 text-center text-[11px] text-ink-muted">
                  {band}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {rows.length > 0 ? (
          <table className="data mt-4">
            <thead>
              <tr>
                <th>Transaction</th>
                <th>Amount</th>
                <th>Date</th>
                <th>Risk score</th>
                <th>Open anomalies</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.transaction.id}>
                  <td className="mono">{row.transaction.id.slice(0, 8)}…</td>
                  <td className="mono">
                    {row.transaction.amount} {row.transaction.currency}
                  </td>
                  <td className="muted">{row.transaction.transaction_date}</td>
                  <td>
                    {row.score ? (
                      <span className="flex items-center gap-2">
                        <strong>{row.score.score}</strong>
                        <SeverityChip severity={severityBand(Number(row.score.score))} />
                      </span>
                    ) : (
                      <span className="muted">Not scored</span>
                    )}
                  </td>
                  <td className={row.openAnomalyCount > 0 ? "text-risk-critical" : "muted"}>
                    {row.openAnomalyCount}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </div>
  );
}
