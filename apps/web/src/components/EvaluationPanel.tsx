"use client";

/**
 * Evaluation — real aggregate analytics over this engagement's agent runs and HITL decisions.
 * Deliberately not a golden-dataset grading harness: agent-orchestrator has no labeled answer key
 * to score runs against, so every number here is a straight count over `agent.runs` /
 * `agent.hitl_interrupts` rows the AI Copilot tab already writes — run outcome distribution
 * overall and per use case, and how often a human reviewer approves vs. rejects vs. edits a run's
 * draft (see agent-orchestrator's application/evaluation.py for the full reasoning).
 */
import { useQuery } from "@tanstack/react-query";

import { bffGet } from "@/lib/client-api";
import type { EvaluationMetrics } from "@/lib/types";
import { ErrorNotice, StatusText } from "@/components/ui";

function StatusCounts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return <span className="muted">—</span>;
  return (
    <span className="inline-flex flex-wrap gap-2.5">
      {entries.map(([status, count]) => (
        <span key={status}>
          <StatusText status={status} /> <span className="mono">{count}</span>
        </span>
      ))}
    </span>
  );
}

export function EvaluationPanel({ engagementId }: { engagementId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["evaluation-metrics", engagementId],
    queryFn: () => bffGet<EvaluationMetrics>(`/api/bff/engagements/${engagementId}/evaluation`),
  });

  return (
    <div className="panel-stack">
      <section>
        <h2>Evaluation</h2>
        <p className="lede mt-1">
          How this engagement&apos;s AI Copilot runs are actually going: run outcomes overall and
          per use case, plus how often a human reviewer approves, edits, or rejects a run&apos;s
          draft.
          Every number here is a real count over runs and HITL decisions already recorded on the
          AI Copilot tab — not a benchmark score against any assumed correct answer.
        </p>

        {isLoading ? <p className="muted mt-4">Loading evaluation metrics…</p> : null}
        {error ? (
          <div className="mt-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}

        {data && data.total_runs === 0 ? (
          <p className="muted mt-4">
            No agent runs yet. Start one on the AI Copilot tab to see evaluation metrics here.
          </p>
        ) : null}

        {data && data.total_runs > 0 ? (
          <>
            <div className="mb-8 mt-4 flex flex-wrap gap-10">
              <Metric label="Total runs" value={data.total_runs} />
              <Metric label="HITL checkpoints" value={data.total_interrupts} />
              <Metric label="Awaiting review" value={data.open_interrupts} />
              <Metric label="Reviewed" value={data.resolved_interrupts} />
            </div>

            {data.approval_rate ? (
              <>
                <h3>Approval rate</h3>
                <p className="muted mt-1.5">
                  A non-parametric bootstrap 95% confidence interval, not a raw percentage — a
                  wide interval at this sample size is the statistically honest result, not a
                  placeholder.
                </p>
                <div className="mt-3">
                  <span className="text-3xl font-bold">
                    {(data.approval_rate.point_estimate * 100).toFixed(0)}%
                  </span>
                  <span className="muted ml-2.5">
                    95% CI [{(data.approval_rate.ci_low * 100).toFixed(0)}%,{" "}
                    {(data.approval_rate.ci_high * 100).toFixed(0)}%] · n=
                    {data.approval_rate.sample_size}
                  </span>
                </div>
              </>
            ) : null}

            <h3 className="mt-6">Run outcomes</h3>
            <p className="muted mt-1.5">
              <StatusCounts counts={data.run_status_counts} />
            </p>

            <h3 className="mt-6">By use case</h3>
            {data.use_case_breakdown.length === 0 ? (
              <p className="muted mt-1.5">No runs yet.</p>
            ) : (
              <table className="data mt-1.5">
                <thead>
                  <tr>
                    <th>Use case</th>
                    <th>Outcomes</th>
                  </tr>
                </thead>
                <tbody>
                  {data.use_case_breakdown.map((row) => (
                    <tr key={row.use_case}>
                      <td className="mono">{row.use_case}</td>
                      <td>
                        <StatusCounts counts={row.status_counts} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <h3 className="mt-6">Reviewer decisions</h3>
            {data.resolved_interrupts === 0 ? (
              <p className="muted mt-1.5">No HITL checkpoints resolved yet.</p>
            ) : (
              <p className="muted mt-1.5">
                <StatusCounts counts={data.decision_counts} />
              </p>
            )}

            {data.recent_reject_edit_reasons.length > 0 ? (
              <>
                <h3 className="mt-6">Recent reject / edit reasons</h3>
                <table className="data mt-1.5">
                  <thead>
                    <tr>
                      <th>Decision</th>
                      <th>Reason</th>
                      <th>Resolved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_reject_edit_reasons.map((interrupt) => (
                      <tr key={interrupt.id}>
                        <td>
                          <StatusText status={interrupt.decision ?? ""} />
                        </td>
                        <td className="muted">{interrupt.reason}</td>
                        <td className="muted">
                          {interrupt.resolved_at
                            ? new Date(interrupt.resolved_at).toLocaleString()
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : null}
          </>
        ) : null}
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-3xl font-bold">{value}</div>
      <div className="muted mt-0.5">{label}</div>
    </div>
  );
}
