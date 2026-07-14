"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { bffGet, bffPost } from "@/lib/client-api";
import type { Finding, FindingSeverity } from "@/lib/types";
import { useToast } from "@/components/Toast";
import { ErrorNotice, SeverityChip, SkeletonCards, StatusText } from "@/components/ui";

const SEVERITIES: readonly FindingSeverity[] = ["critical", "high", "medium", "low"];

export function FindingsPanel({ engagementId }: { engagementId: string }) {
  const findingsKey = ["findings", engagementId];
  const { data, isLoading, error } = useQuery({
    queryKey: findingsKey,
    queryFn: () => bffGet<Finding[]>(`/api/bff/engagements/${engagementId}/findings`),
  });

  return (
    <div className="panel-stack">
      <section>
        <h2>Findings</h2>
        <p className="lede mt-1">
          Every finding passes through a <strong>mandatory human sign-off gate</strong>: it
          is created as a <em>draft</em>, then an Auditor or Fraud Analyst must confirm or reject it.
          Only those two roles may operate the gate — if your account isn&apos;t permitted, the
          confirm/reject action returns a clear error rather than being hidden.
        </p>
      </section>

      <CreateFindingForm engagementId={engagementId} findingsKey={findingsKey} />

      <div>
        {isLoading ? <SkeletonCards count={3} /> : null}
        {error ? <ErrorNotice error={error} /> : null}
        {data && data.length === 0 ? (
          <p className="muted">No findings yet. Draft one above.</p>
        ) : null}
        {data && data.length > 0 ? (
          <div className="grid gap-3">
            {data.map((finding) => (
              <FindingCard key={finding.id} finding={finding} findingsKey={findingsKey} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CreateFindingForm({
  engagementId,
  findingsKey,
}: {
  engagementId: string;
  findingsKey: string[];
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState<FindingSeverity>("high");
  const [controlId, setControlId] = useState("");

  const create = useMutation({
    mutationFn: () =>
      bffPost<Finding>(`/api/bff/engagements/${engagementId}/findings`, {
        title,
        description,
        severity,
        control_id: controlId.trim() === "" ? null : controlId.trim(),
      }),
    onSuccess: (finding) => {
      toast.show(`Draft finding "${finding.title}" created — awaiting sign-off.`);
      setTitle("");
      setDescription("");
      setControlId("");
      void queryClient.invalidateQueries({ queryKey: findingsKey });
    },
  });

  return (
    <section className="card">
      <h3>Draft a new finding</h3>
      <p className="muted mt-1.5">
        Available to Auditor, Fraud Analyst, and Compliance Manager.
      </p>
      <form
        className="mt-2.5 grid gap-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim() && description.trim()) create.mutate();
        }}
      >
        <input
          className="field"
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="Finding title"
        />
        <textarea
          className="field"
          placeholder="Description"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          aria-label="Finding description"
        />
        <div className="flex flex-wrap gap-2.5">
          <select
            className="field max-w-[160px]"
            value={severity}
            onChange={(e) => setSeverity(e.target.value as FindingSeverity)}
            aria-label="Severity"
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            className="field max-w-[200px]"
            placeholder="Control id (optional)"
            value={controlId}
            onChange={(e) => setControlId(e.target.value)}
            aria-label="Control id"
          />
          <button className="btn btn-primary" type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create draft"}
          </button>
        </div>
      </form>
      {create.error ? (
        <div className="mt-3">
          <ErrorNotice error={create.error} />
        </div>
      ) : null}
    </section>
  );
}

function FindingCard({ finding, findingsKey }: { finding: Finding; findingsKey: string[] }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: findingsKey });

  const confirm = useMutation({
    mutationFn: () =>
      bffPost<Finding>(
        `/api/bff/engagements/${finding.engagement_id}/findings/${finding.id}/confirm`,
      ),
    onSuccess: () => {
      toast.show(`"${finding.title}" confirmed.`);
      void invalidate();
    },
  });

  const reject = useMutation({
    mutationFn: () =>
      bffPost<Finding>(
        `/api/bff/engagements/${finding.engagement_id}/findings/${finding.id}/reject`,
        { disposition_reason: reason },
      ),
    onSuccess: () => {
      toast.show(`"${finding.title}" rejected.`);
      setRejectOpen(false);
      setReason("");
      void invalidate();
    },
  });

  const isDraft = finding.status === "draft";
  const gateError = confirm.error ?? reject.error;

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <SeverityChip severity={finding.severity} />
            <strong>{finding.title}</strong>
          </div>
          <p className="mb-1 mt-1.5">{finding.description}</p>
          <div className="muted">
            Status: <StatusText status={finding.status} />
            {finding.control_id ? ` · control ${finding.control_id}` : ""}
            {finding.disposition_reason
              ? ` · rejected: ${finding.disposition_reason}`
              : ""}
          </div>
        </div>
      </div>

      {/* The human sign-off gate — only meaningful while the finding is a draft. */}
      {isDraft ? (
        <div className="mt-4">
          <div className="notice warn mb-2.5">
            Awaiting human sign-off. An Auditor or Fraud Analyst must confirm or reject this draft
            before it can appear in a report.
          </div>
          <div className="flex flex-wrap gap-2.5">
            <button
              className="btn btn-primary"
              onClick={() => confirm.mutate()}
              disabled={confirm.isPending}
            >
              {confirm.isPending ? "Confirming…" : "Confirm"}
            </button>
            <button
              className="btn btn-danger"
              onClick={() => setRejectOpen((v) => !v)}
              disabled={reject.isPending}
            >
              Reject…
            </button>
          </div>
          {rejectOpen ? (
            <div className="fade-in mt-2.5">
              <textarea
                className="field"
                rows={2}
                placeholder="Documented reason for rejection (required)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                aria-label="Rejection reason"
              />
              <button
                className="btn btn-danger mt-2"
                onClick={() => reject.mutate()}
                disabled={reason.trim().length === 0 || reject.isPending}
              >
                {reject.isPending ? "Rejecting…" : "Confirm rejection"}
              </button>
            </div>
          ) : null}
          {gateError ? (
            <div className="mt-3">
              <ErrorNotice error={gateError} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
