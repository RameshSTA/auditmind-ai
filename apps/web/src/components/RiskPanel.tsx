"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { bffGet, bffPost } from "@/lib/client-api";
import type { Anomaly, ModelValidationResult, RiskScore, TransactionRecord } from "@/lib/types";
import { useToast } from "@/components/Toast";
import { ErrorNotice, SeverityChip, SkeletonCards, SkeletonTable, StatusText } from "@/components/ui";

export function RiskPanel({ engagementId }: { engagementId: string }) {
  return (
    <div className="panel-stack">
      <TransactionsSection engagementId={engagementId} />
      <AnomaliesSection engagementId={engagementId} />
      <RiskScoresSection engagementId={engagementId} />
      <ModelValidationSection engagementId={engagementId} />
    </div>
  );
}

const SAMPLE_TRANSACTIONS = `[
  { "amount": "9800.00", "currency": "USD", "transaction_date": "2026-01-15", "vendor_name": "Acme Supplies" },
  { "amount": "9700.00", "currency": "USD", "transaction_date": "2026-01-16", "vendor_name": "Acme Supplies" }
]`;

function TransactionsSection({ engagementId }: { engagementId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const transactionsKey = ["transactions", engagementId];
  const { data, isLoading, error } = useQuery({
    queryKey: transactionsKey,
    queryFn: () => bffGet<TransactionRecord[]>(`/api/bff/engagements/${engagementId}/transactions`),
  });

  const importTx = useMutation({
    mutationFn: (transactions: unknown[]) =>
      bffPost<TransactionRecord[]>(`/api/bff/engagements/${engagementId}/transactions`, {
        transactions,
      }),
    onSuccess: (created) => {
      toast.show(`Imported ${created.length} transaction${created.length === 1 ? "" : "s"}.`);
      setDraft("");
      void queryClient.invalidateQueries({ queryKey: transactionsKey });
      void queryClient.invalidateQueries({ queryKey: ["model-validation", engagementId] });
    },
  });

  function handleImport() {
    setParseError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      setParseError("Not valid JSON — expected an array of transaction objects.");
      return;
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      setParseError("Expected a non-empty JSON array of transaction objects.");
      return;
    }
    importTx.mutate(parsed);
  }

  return (
    <section>
      <h2>Transactions</h2>
      <p className="lede mt-1">
        Bulk-imported transaction records — the source data the rule engine and the fraud-scoring
        ensemble below both read. There is no ERP connector yet, so this JSON paste is the only
        way transaction data enters the platform.
      </p>

      <div className="card mt-4 mb-4">
        <label className="kicker" htmlFor="tx-import">
          Import transactions (JSON array)
        </label>
        <textarea
          id="tx-import"
          className="field mono mt-1.5 min-h-[110px]"
          placeholder={SAMPLE_TRANSACTIONS}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="mt-2.5">
          <button
            className="btn btn-primary"
            onClick={handleImport}
            disabled={importTx.isPending || draft.trim().length === 0}
          >
            {importTx.isPending ? "Importing…" : "Import"}
          </button>
        </div>
        {parseError ? (
          <div className="notice error mt-3" role="alert">
            {parseError}
          </div>
        ) : null}
        {importTx.error ? (
          <div className="mt-3">
            <ErrorNotice error={importTx.error} />
          </div>
        ) : null}
      </div>

      {isLoading ? <SkeletonTable rows={3} cols={3} /> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <p className="muted">No transactions imported yet.</p>
      ) : null}
      {data && data.length > 0 ? (
        <table className="data">
          <thead>
            <tr>
              <th>Amount</th>
              <th>Date</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {data.map((tx) => (
              <tr key={tx.id}>
                <td className="mono">
                  {tx.amount} {tx.currency}
                </td>
                <td className="muted">{tx.transaction_date}</td>
                <td className="mono">{tx.source_system}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function AnomaliesSection({ engagementId }: { engagementId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const anomaliesKey = ["anomalies", engagementId];
  const { data, isLoading, error } = useQuery({
    queryKey: anomaliesKey,
    queryFn: () => bffGet<Anomaly[]>(`/api/bff/engagements/${engagementId}/anomalies`),
  });

  const scan = useMutation({
    mutationFn: () => bffPost<Anomaly[]>(`/api/bff/engagements/${engagementId}/risk/scan`),
    onSuccess: (anomalies) => {
      toast.show(`Scan complete — ${anomalies.length} anomal${anomalies.length === 1 ? "y" : "ies"} flagged.`);
      void queryClient.invalidateQueries({ queryKey: anomaliesKey });
      void queryClient.invalidateQueries({ queryKey: ["model-validation", engagementId] });
    },
  });

  return (
    <section>
      <div className="panel-header-row">
        <h2>Anomalies</h2>
        <button className="btn" onClick={() => scan.mutate()} disabled={scan.isPending}>
          {scan.isPending ? "Scanning…" : "Scan for anomalies"}
        </button>
      </div>
      <p className="lede mt-1">
        Flagged by the rule engine — Benford&apos;s Law, duplicate-payment matching, and
        threshold/round-dollar detection. Each carries the same human-review gate as findings: an
        Auditor or Fraud Analyst confirms or dismisses it.
      </p>
      {scan.error ? (
        <div className="mt-3">
          <ErrorNotice error={scan.error} />
        </div>
      ) : null}

      {isLoading ? <SkeletonCards count={2} /> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <p className="muted mt-4">No anomalies flagged for this engagement.</p>
      ) : null}
      {data && data.length > 0 ? (
        <div className="mt-4 grid gap-3">
          {data.map((anomaly) => (
            <AnomalyCard key={anomaly.id} anomaly={anomaly} anomaliesKey={anomaliesKey} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

const ANOMALY_TYPE_LABELS: Record<string, string> = {
  benford_deviation: "Benford's Law deviation",
  duplicate_payment: "Duplicate payment",
  threshold_structuring: "Near-threshold amount",
  round_dollar: "Round-dollar amount",
};

/** Renders each rule engine anomaly type's details as a plain-English sentence instead of a raw
 * JSON dump — the shapes come straight from risk/application/rules.py's four detectors, so a
 * type not listed here (a future fifth detector) falls back to a labeled key/value list rather
 * than ever showing bracket/brace syntax to a reader. */
function AnomalyDetails({ anomaly }: { anomaly: Anomaly }) {
  const d = anomaly.details;
  switch (anomaly.anomaly_type) {
    case "duplicate_payment":
      return (
        <p className="mt-2 text-sm text-ink-secondary">
          Paid <strong className="text-ink">{String(d.amount)}</strong> twice —{" "}
          {d.days_apart === 0 ? "same day as" : `${String(d.days_apart)} day(s) after`} an earlier
          payment (transaction <span className="mono">{String(d.duplicate_of_transaction_id).slice(0, 8)}…</span>).
        </p>
      );
    case "threshold_structuring":
      return (
        <p className="mt-2 text-sm text-ink-secondary">
          <strong className="text-ink">{String(d.amount)}</strong> falls within 5% of the{" "}
          <strong className="text-ink">{String(d.threshold)}</strong> approval threshold — consistent
          with sizing a payment to avoid the extra scrutiny a larger amount would trigger.
        </p>
      );
    case "round_dollar":
      return (
        <p className="mt-2 text-sm text-ink-secondary">
          <strong className="text-ink">{String(d.amount)}</strong> is an exact multiple of
          $1,000 — legitimate transactions are rarely this round.
        </p>
      );
    case "benford_deviation": {
      const dist = d.observed_distribution as Record<string, number> | undefined;
      return (
        <div className="mt-2 text-sm text-ink-secondary">
          <p>
            The leading-digit distribution across{" "}
            <strong className="text-ink">{String(d.sample_size)}</strong> transactions deviates
            from Benford&apos;s Law (mean absolute deviation{" "}
            <span className="mono">{Number(d.mean_absolute_deviation).toFixed(4)}</span>) — a
            statistical signature of naturally-occurring amounts, so a strong deviation is
            consistent with fabricated or manually-entered figures.
          </p>
          {dist ? (
            <div className="mt-2 flex gap-3">
              {Object.entries(dist).map(([digit, count]) => (
                <span key={digit} className="mono text-xs text-ink-muted">
                  {digit}: {count}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      );
    }
    default:
      return (
        <div className="mt-2 grid gap-0.5 text-sm text-ink-secondary">
          {Object.entries(d).map(([key, value]) => (
            <span key={key}>
              <span className="mono text-ink-muted">{key}:</span> {String(value)}
            </span>
          ))}
        </div>
      );
  }
}

function AnomalyCard({ anomaly, anomaliesKey }: { anomaly: Anomaly; anomaliesKey: string[] }) {
  const cardQueryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () => cardQueryClient.invalidateQueries({ queryKey: anomaliesKey });

  const disposition = useMutation({
    mutationFn: (status: "true_positive" | "false_positive") =>
      bffPost<Anomaly>(
        `/api/bff/engagements/${anomaly.engagement_id}/anomalies/${anomaly.id}/disposition`,
        { status },
      ),
    onSuccess: (_updated, status) => {
      toast.show(`Anomaly marked ${status === "true_positive" ? "true positive" : "false positive"}.`);
      void invalidate();
    },
  });

  const isOpen = anomaly.status === "open";

  return (
    <div className="card">
      <div className="flex items-center gap-2">
        <SeverityChip severity={anomaly.severity} />
        <strong>{ANOMALY_TYPE_LABELS[anomaly.anomaly_type] ?? anomaly.anomaly_type}</strong>
        <span className="muted">
          · <StatusText status={anomaly.status} />
        </span>
      </div>
      <AnomalyDetails anomaly={anomaly} />
      {isOpen ? (
        <div className="mt-3 flex flex-wrap gap-2.5">
          <button
            className="btn btn-primary"
            onClick={() => disposition.mutate("true_positive")}
            disabled={disposition.isPending}
          >
            Confirm
          </button>
          <button
            className="btn"
            onClick={() => disposition.mutate("false_positive")}
            disabled={disposition.isPending}
          >
            Dismiss
          </button>
        </div>
      ) : null}
      {disposition.error ? (
        <div className="mt-3">
          <ErrorNotice error={disposition.error} />
        </div>
      ) : null}
    </div>
  );
}

function RiskScoresSection({ engagementId }: { engagementId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const scoresKey = ["risk-scores", engagementId];
  const { data, isLoading, error } = useQuery({
    queryKey: scoresKey,
    queryFn: () => bffGet<RiskScore[]>(`/api/bff/engagements/${engagementId}/risk-scores`),
  });

  const compute = useMutation({
    mutationFn: () => bffPost<RiskScore[]>(`/api/bff/engagements/${engagementId}/risk/score`),
    onSuccess: (scores) => {
      toast.show(`Computed risk scores for ${scores.length} transaction${scores.length === 1 ? "" : "s"}.`);
      void queryClient.invalidateQueries({ queryKey: scoresKey });
      void queryClient.invalidateQueries({ queryKey: ["model-validation", engagementId] });
    },
  });

  return (
    <section>
      <div className="panel-header-row">
        <h2>Risk scores</h2>
        <button className="btn" onClick={() => compute.mutate()} disabled={compute.isPending}>
          {compute.isPending ? "Computing…" : "Compute risk scores"}
        </button>
      </div>
      <p className="lede mt-1">
        Composite fraud-risk score per transaction from the full ensemble — rule engine, Isolation
        Forest, HDBSCAN cohort clustering, and graph centrality. Recomputing refreshes each
        transaction&apos;s score in place.
      </p>
      {compute.error ? (
        <div className="mt-3">
          <ErrorNotice error={compute.error} />
        </div>
      ) : null}
      {isLoading ? <SkeletonTable rows={3} cols={5} /> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <p className="muted mt-4">
          No risk scores computed yet for this engagement&apos;s transactions.
        </p>
      ) : null}
      {data && data.length > 0 ? (
        <table className="data mt-4">
          <thead>
            <tr>
              <th>Subject</th>
              <th>Score</th>
              <th>Version</th>
              <th>Computed</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.map((score) => (
              <RiskScoreRow key={score.id} score={score} />
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

const SIGNAL_LABELS: Record<string, string> = {
  rule_engine: "Rule engine",
  isolation_forest: "Isolation Forest",
  hdbscan_cohort: "HDBSCAN cohort",
  graph_centrality: "Graph centrality",
};

function RiskScoreRow({ score }: { score: RiskScore }) {
  const [expanded, setExpanded] = useState(false);
  const signalEntries = Object.entries(score.contributing_factors).filter(
    ([name]) => name !== "final_score",
  ) as [string, { value: number; weight: number }][];

  return (
    <>
      <tr>
        <td className="mono">
          {score.subject_type} {score.subject_id.slice(0, 8)}…
        </td>
        <td>
          <strong>{score.score}</strong>
        </td>
        <td className="mono">{score.score_version}</td>
        <td className="muted">{new Date(score.computed_at).toLocaleString()}</td>
        <td>
          {signalEntries.length > 0 ? (
            <button className="btn" onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Hide breakdown" : "Why this score?"}
            </button>
          ) : null}
        </td>
      </tr>
      {expanded && signalEntries.length > 0 ? (
        <tr>
          <td colSpan={5}>
            <div className="card fade-in max-w-[480px]">
              <div className="kicker mb-2.5">
                Contributing signals — only signals that could actually be computed for this
                subject are shown; a signal missing here means it wasn&apos;t excluded as zero, it
                genuinely wasn&apos;t available (e.g. below the ML minimum sample size).
              </div>
              {signalEntries.map(([name, factor]) => (
                <div key={name} className="flex justify-between gap-4 py-1.5">
                  <span className="mono">{SIGNAL_LABELS[name] ?? name}</span>
                  <span className="mono muted">
                    value {factor.value} · weight {factor.weight}
                  </span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ModelValidationSection({ engagementId }: { engagementId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["model-validation", engagementId],
    queryFn: () =>
      bffGet<ModelValidationResult>(
        `/api/bff/engagements/${engagementId}/risk/model-validation`,
      ),
  });

  return (
    <section>
      <h2>Model validation</h2>
      <p className="lede mt-1">
        Cross-validated performance of the Isolation Forest and HDBSCAN signals, and an ablation
        of the combiner&apos;s fixed weights — computed live over this engagement&apos;s real
        transactions, anomalies, and risk scores.
      </p>
      <div className="notice mt-3">
        This demo has no independently-labeled fraud ground truth. Every metric below treats the
        rule engine&apos;s own flagged anomalies as a proxy positive label — a high ROC-AUC means
        the ML signals agree with the deterministic rules, not that they&apos;ve been validated
        against real-world fraud outcomes.
      </div>

      {isLoading ? <p className="muted mt-4">Computing validation metrics…</p> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}

      {data ? (
        <div className="mt-4">
          <p className="muted">
            {data.transaction_count} transactions · {data.flagged_count} rule-engine flagged
          </p>

          {!data.isolation_forest && !data.hdbscan_stability ? (
            <p className="muted mt-3">
              Not enough transactions yet (need at least 10, and at least one rule-engine-flagged
              anomaly) to compute cross-validated metrics — import more transactions and run a
              scan first.
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-8">
            {data.isolation_forest ? (
              <>
                <Metric
                  label="Isolation Forest ROC-AUC"
                  value={data.isolation_forest.roc_auc_mean.toFixed(3)}
                  sub={`± ${data.isolation_forest.roc_auc_std.toFixed(3)} · ${data.isolation_forest.fold_count} folds`}
                />
                <Metric
                  label="Precision @ p90"
                  value={data.isolation_forest.precision_at_p90_mean.toFixed(3)}
                />
                <Metric
                  label="Recall @ p90"
                  value={data.isolation_forest.recall_at_p90_mean.toFixed(3)}
                />
              </>
            ) : null}
            {data.hdbscan_stability ? (
              <Metric
                label="HDBSCAN noise fraction"
                value={data.hdbscan_stability.noise_fraction_mean.toFixed(2)}
                sub={`± ${data.hdbscan_stability.noise_fraction_std.toFixed(2)} over ${data.hdbscan_stability.resample_count} resamples`}
              />
            ) : null}
            {data.baseline_combined_auc !== null ? (
              <Metric
                label="Combined score ROC-AUC"
                value={data.baseline_combined_auc.toFixed(3)}
              />
            ) : null}
          </div>

          {data.combiner_ablation.length > 0 ? (
            <>
              <h3 className="mt-6">Combiner weight ablation</h3>
              <p className="muted mt-1.5">
                ROC-AUC if a signal&apos;s weight were zeroed out and redistributed to the rest —
                a large negative delta means that signal is doing real work in the ensemble.
              </p>
              <table className="data mt-1.5">
                <thead>
                  <tr>
                    <th>Without signal</th>
                    <th>ROC-AUC</th>
                    <th>Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {data.combiner_ablation.map((entry) => (
                    <tr key={entry.signal_name}>
                      <td className="mono">{SIGNAL_LABELS[entry.signal_name] ?? entry.signal_name}</td>
                      <td className="mono">{entry.auc_without_signal.toFixed(3)}</td>
                      <td className={`mono ${entry.delta < 0 ? "text-risk-critical" : ""}`}>
                        {entry.delta >= 0 ? "+" : ""}
                        {entry.delta.toFixed(3)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-3xl font-bold">{value}</div>
      <div className="muted mt-0.5">{label}</div>
      {sub ? <div className="muted text-xs mt-0.5">{sub}</div> : null}
    </div>
  );
}
