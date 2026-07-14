/** Small presentational primitives shared across pages. Framework-free, no client state. */
import type { FindingSeverity } from "@/lib/types";
import type { BffError } from "@/lib/client-api";

/**
 * Severity chip. Colour is never the ONLY signal — the label text carries the severity too, so a
 * colour-blind or monochrome reader loses nothing.
 */
export function SeverityChip({ severity }: { severity: FindingSeverity }) {
  return <span className={`chip sev-${severity}`}>{severity}</span>;
}

export function StatusText({ status }: { status: string }) {
  return <span className={`status-${status}`}>{status}</span>;
}

/** Initials derived from a real display name — never a placeholder image or a fabricated avatar,
 * the same "no invented content" discipline the rest of the product follows. */
export function Avatar({ name }: { name: string }) {
  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <span className="avatar" aria-hidden="true">
      {initials || "?"}
    </span>
  );
}

/**
 * Error notice. Renders the backend's RFC 7807 message and trace_id when present — the trace_id
 * is bound to every request so a user can quote it to support, so surfacing it here (not hiding
 * it) is the point, not a leak.
 *
 * A 403 gets its own calmer framing ("Restricted", not "error") — access control correctly
 * denying a request is expected, routine platform behavior, not a broken page, and shouldn't read
 * like one.
 */
export function ErrorNotice({ error }: { error: unknown }) {
  const bff = error as Partial<BffError> & { message?: string };
  const problem = bff?.problem ?? null;
  const isForbidden = bff?.status === 403;

  return (
    <div className={`notice ${isForbidden ? "warn" : "error"}`} role="alert">
      <strong>{isForbidden ? "Restricted" : (problem?.title ?? "Something went wrong")}</strong>
      <div>{problem?.detail ?? bff?.message ?? "Unexpected error."}</div>
      {problem?.trace_id ? (
        <div className="mono mt-2 opacity-80">trace: {problem.trace_id}</div>
      ) : null}
    </div>
  );
}

/** A single pulsing placeholder bar — the shape (width/height) is the caller's job via className. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

/** Row-shaped loading placeholder for `table.data` — replaces bare "Loading…" text so a table's
 * layout doesn't jump once real rows arrive. */
export function SkeletonTable({ rows = 3, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="mt-4 grid gap-2.5" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className="h-4 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Card-shaped loading placeholder for the `.card` grids (Findings, Anomalies). */
export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="mt-4 grid gap-3" role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-24 w-full" />
      ))}
    </div>
  );
}
