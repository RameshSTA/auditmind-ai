"use client";

/**
 * Reports — compiles every currently-confirmed finding into a new versioned, immutable report
 * (FR-8.1). PDF export is real: the backend renders it (fpdf2, from the same confirmed
 * findings/cited evidence the on-screen report reads from) lazily on first download and serves
 * the same stored file after that — the "Download PDF" link below is a plain anchor tag, not a
 * fetch-then-blob dance, since the browser's native download handling is simpler and more
 * reliable than reimplementing it in JS. "Print / Save as PDF" stays too, as the free instant
 * alternative (the browser's own print pipeline, styled by the print stylesheet in globals.css).
 *
 * The report body is real evidence-cited Markdown rendered by the backend (each finding's
 * description plus its actual cited passages and source chunk, or an explicit "none attached"
 * rather than fabricating a citation) — this panel renders it, it does not compose it. The custom
 * component overrides below exist only to give that same content a proper document layout
 * (letterhead, severity distribution, flagged evidence gaps) — every number they show is parsed
 * back out of the backend's own rendered text, nothing here is computed independently of it.
 */
import { Children, isValidElement, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { Download, Printer } from "lucide-react";

import { bffGet, bffPost } from "@/lib/client-api";
import type { FindingSeverity, Report } from "@/lib/types";
import { useToast } from "@/components/Toast";
import { ErrorNotice, Skeleton, SkeletonTable, StatusText } from "@/components/ui";

const _SEVERITY_HEADING = /^\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s*(.*)$/;
const _SUMMARY_LINE = /^Executive summary: (\d+) confirmed finding\(s\) — (.+)\.$/;
const _STATUS_LINE = /^Status: (\S+)(?: · Control: (.+))?$/;
const _EVIDENCE_HEADING = /^Evidence \(\d+ citations?\):$/;
const _NO_EVIDENCE_LINE = "Evidence: none attached.";
const _SOURCE_CHUNK_LINE = /^— source chunk/;

const SEVERITY_ORDER: readonly FindingSeverity[] = ["critical", "high", "medium", "low"];
const SEVERITY_BAR_COLOR: Record<FindingSeverity, string> = {
  critical: "rgb(var(--risk-critical))",
  high: "rgb(var(--risk-high))",
  medium: "rgb(var(--risk-medium))",
  low: "rgb(var(--risk-low))",
};

/** Flattens a ReactMarkdown-produced element tree back to plain text — used to recognize which
 * paragraph/heading the backend generated regardless of the inline markdown (bold/italic/code)
 * wrapping it. */
function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode };
    return nodeText(props.children);
  }
  return "";
}

/**
 * The backend renders each finding's heading as ``## [SEVERITY] Title`` — this pulls that tag back
 * out so it can render as the same severity chip every other finding list in the app uses, rather
 * than showing raw bracket syntax to the reader.
 */
function ReportFindingHeading({ children }: { children?: ReactNode }) {
  const text = nodeText(children);
  const match = _SEVERITY_HEADING.exec(text);
  if (!match) {
    return <h2 className="report-heading">{text}</h2>;
  }
  const [, tag, title] = match;
  const severity = (tag ?? "").toLowerCase() as FindingSeverity;
  return (
    <h2 className="report-heading">
      <span className={`chip sev-${severity}`}>{tag}</span> {title}
    </h2>
  );
}

/** A compact severity distribution bar — the same visual language as the Dashboard's and Fraud
 * Detection's own severity charts, so a reader who has seen either already knows how to read it. */
function SeverityBar({ counts, total }: { counts: Partial<Record<FindingSeverity, number>>; total: number }) {
  return (
    <div className="report-severity-bar" role="img" aria-label={`${total} confirmed findings by severity`}>
      {SEVERITY_ORDER.map((severity) => {
        const count = counts[severity] ?? 0;
        if (count === 0) return null;
        return (
          <div
            key={severity}
            className="report-severity-segment"
            style={{ width: `${(count / total) * 100}%`, background: SEVERITY_BAR_COLOR[severity] }}
            title={`${count} ${severity}`}
          />
        );
      })}
    </div>
  );
}

/** Replaces the backend's plain "Executive summary: …" sentence with the same severity-bar
 * treatment used elsewhere in the product — parsed from that exact sentence, not recomputed, so it
 * can never show a number the underlying report text doesn't also say in words. */
function ReportExecutiveSummary({ text }: { text: string }) {
  const match = _SUMMARY_LINE.exec(text);
  if (!match) return <p className="lede">{text}</p>;
  const [, totalText, breakdown] = match;
  const total = Number(totalText);
  const counts: Partial<Record<FindingSeverity, number>> = {};
  for (const entry of breakdown!.matchAll(/(\d+) (critical|high|medium|low)/g)) {
    counts[entry[2] as FindingSeverity] = Number(entry[1]);
  }
  return (
    <div className="report-summary-card">
      <div className="kicker">Executive summary</div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-3xl font-bold">{total}</span>
        <span className="muted">confirmed finding{total === 1 ? "" : "s"}</span>
      </div>
      <SeverityBar counts={counts} total={total} />
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
        {SEVERITY_ORDER.filter((s) => (counts[s] ?? 0) > 0).map((severity) => (
          <span key={severity} className="muted text-xs">
            <span className={`chip sev-${severity}`}>{severity}</span> {counts[severity]}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Reclassifies the backend's plain-text paragraphs into the right visual treatment: the executive
 * summary becomes the card above, a "Status: … · Control: …" line becomes a real StatusText chip
 * instead of italic prose, an evidence-count line becomes a section label, and a bare "none
 * attached" becomes a visible caveat — not blended into the same gray text as everything else,
 * because an unsupported finding is exactly the thing a reviewer must not miss. */
function ReportParagraph({ children }: { children?: ReactNode }) {
  const text = nodeText(children).trim();

  if (_SUMMARY_LINE.test(text)) return <ReportExecutiveSummary text={text} />;

  if (text === _NO_EVIDENCE_LINE) {
    return (
      <div className="notice warn report-evidence-gap">
        No evidence attached — this finding is not yet supported by cited source evidence.
      </div>
    );
  }

  if (_EVIDENCE_HEADING.test(text)) {
    return <div className="kicker mt-4">{text.replace(/:$/, "")}</div>;
  }

  const statusMatch = _STATUS_LINE.exec(text);
  if (statusMatch) {
    const [, status, controlId] = statusMatch;
    return (
      <p className="report-meta">
        <StatusText status={status!} />
        {controlId ? <span className="mono ml-2 text-ink-muted">Control {controlId}</span> : null}
      </p>
    );
  }

  return <p>{children}</p>;
}

/** Groups a citation quote together with its "— source chunk …" attribution line so the two read
 * as one evidence card, with the attribution styled smaller/monospace rather than more italic
 * prose indistinguishable from the quote itself. */
function ReportEvidenceQuote({ children }: { children?: ReactNode }) {
  const items = Children.toArray(children).filter((child) => isValidElement(child));
  const last = items[items.length - 1];
  const isAttributed = items.length > 1 && last !== undefined && _SOURCE_CHUNK_LINE.test(nodeText(last).trim());
  return (
    <blockquote className="report-evidence">
      {isAttributed ? items.slice(0, -1) : items}
      {isAttributed ? <p className="report-evidence-source mono">{nodeText(last)}</p> : null}
    </blockquote>
  );
}

const REPORT_MARKDOWN_COMPONENTS = {
  // The backend's own title line ("# Audit Report — Engagement <uuid> (v1)") duplicates the
  // letterhead card rendered above the Markdown body, and leads with a raw uuid — suppressed here
  // rather than shown twice with the ugly id front and center.
  h1: () => null,
  h2: ReportFindingHeading,
  p: ReportParagraph,
  blockquote: ReportEvidenceQuote,
};

export function ReportsPanel({ engagementId }: { engagementId: string }) {
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();
  const reportsKey = ["reports", engagementId];

  const { data, isLoading, error } = useQuery({
    queryKey: reportsKey,
    queryFn: () => bffGet<Report[]>(`/api/bff/engagements/${engagementId}/reports`),
  });

  const generate = useMutation({
    mutationFn: () => bffPost<Report>(`/api/bff/engagements/${engagementId}/reports`),
    onSuccess: (report) => {
      toast.show(`Report v${report.version} generated.`);
      void queryClient.invalidateQueries({ queryKey: reportsKey });
      setSelectedReportId(report.id);
    },
  });

  return (
    <div className="panel-stack">
      <section className="no-print">
        <div className="panel-header-row">
          <h2>Reports</h2>
          <button className="btn btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending ? "Generating…" : "Generate report"}
          </button>
        </div>
        <p className="lede mt-1">
          Every confirmed finding at the moment of generation, compiled into a new immutable,
          versioned report. Generating again produces a new version — it never overwrites a prior
          one, so the exact state of confirmed findings at each point in time stays
          reconstructable.
        </p>
        {generate.error ? (
          <div className="mt-3">
            <ErrorNotice error={generate.error} />
          </div>
        ) : null}

        {isLoading ? <SkeletonTable rows={2} cols={4} /> : null}
        {error ? (
          <div className="mt-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}
        {data && data.length === 0 ? (
          <p className="muted mt-4">No reports generated yet.</p>
        ) : null}
        {data && data.length > 0 ? (
          <table className="data mt-4">
            <thead>
              <tr>
                <th>Version</th>
                <th>Findings by severity</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((report) => (
                <ReportListRow
                  key={report.id}
                  engagementId={engagementId}
                  report={report}
                  onView={() => setSelectedReportId(report.id)}
                />
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      {selectedReportId ? (
        <ReportDetailSection
          engagementId={engagementId}
          reportId={selectedReportId}
          onClose={() => setSelectedReportId(null)}
        />
      ) : null}
    </div>
  );
}

function ReportListRow({
  engagementId,
  report,
  onView,
}: {
  engagementId: string;
  report: Report;
  onView: () => void;
}) {
  const match = _SUMMARY_LINE.exec(
    report.body_markdown.split("\n").find((line) => _SUMMARY_LINE.test(line)) ?? "",
  );
  const total = report.finding_ids.length;
  const counts: Partial<Record<FindingSeverity, number>> = {};
  if (match) {
    for (const entry of match[2]!.matchAll(/(\d+) (critical|high|medium|low)/g)) {
      counts[entry[2] as FindingSeverity] = Number(entry[1]);
    }
  }

  return (
    <tr>
      <td className="mono">v{report.version}</td>
      <td>
        {total > 0 ? (
          <div className="flex items-center gap-2.5">
            <SeverityBar counts={counts} total={total} />
            <span className="mono text-xs text-ink-muted">{total}</span>
          </div>
        ) : (
          <span className="muted">0</span>
        )}
      </td>
      <td>
        <div className="flex justify-end gap-2.5">
          <a
            className="btn"
            href={`/api/bff/engagements/${engagementId}/reports/${report.id}/pdf`}
            download={`audit-report-v${report.version}.pdf`}
          >
            <Download size={14} strokeWidth={1.75} aria-hidden="true" className="mr-1.5 inline" />
            PDF
          </a>
          <button className="btn" onClick={onView}>
            View
          </button>
        </div>
      </td>
    </tr>
  );
}

function ReportDetailSection({
  engagementId,
  reportId,
  onClose,
}: {
  engagementId: string;
  reportId: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["report", engagementId, reportId],
    queryFn: () => bffGet<Report>(`/api/bff/engagements/${engagementId}/reports/${reportId}`),
  });

  return (
    <section className="fade-in report-print-area">
      <div className="panel-header-row no-print">
        <h2>{data ? `Report v${data.version}` : "Report"}</h2>
        <div className="flex gap-2.5">
          {data ? (
            <a
              className="btn"
              href={`/api/bff/engagements/${engagementId}/reports/${reportId}/pdf`}
              download={`audit-report-v${data.version}.pdf`}
            >
              <Download size={14} strokeWidth={1.75} aria-hidden="true" className="mr-1.5 inline" />
              Download PDF
            </a>
          ) : null}
          {data ? (
            <button className="btn" onClick={() => window.print()}>
              <Printer size={14} strokeWidth={1.75} aria-hidden="true" className="mr-1.5 inline" />
              Print
            </button>
          ) : null}
          <button className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      {isLoading ? (
        <div className="card mt-4 grid gap-3">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {data ? (
        <div className="card report-letterhead mt-4">
          <div className="kicker">Audit Report</div>
          <div className="report-letterhead-meta mt-2">
            <div>
              <div className="kicker">Generated by</div>
              <div className="mono mt-0.5">{data.generated_by}</div>
            </div>
            <div>
              <div className="kicker">Generated at</div>
              <div className="mono mt-0.5">{new Date(data.generated_at).toLocaleString()}</div>
            </div>
            <div>
              <div className="kicker">Version</div>
              <div className="mono mt-0.5">v{data.version}</div>
            </div>
          </div>
          <div className="report-markdown mt-4">
            <ReactMarkdown components={REPORT_MARKDOWN_COMPONENTS}>{data.body_markdown}</ReactMarkdown>
          </div>
        </div>
      ) : null}
    </section>
  );
}
