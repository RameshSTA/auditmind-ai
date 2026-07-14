"use client";

/**
 * Investigations — a case-building workspace: open an investigation, pull findings, anomalies, and
 * transactions already recorded elsewhere into its working set, and close it with a documented
 * conclusion. Every item is a real finding/anomaly/transaction the caller can already read on the
 * Findings/Risk & Anomalies tabs — this panel adds no new upstream data, only a container and a
 * conclusion on top of what's already there.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { bffDelete, bffGet, bffPost } from "@/lib/client-api";
import type { Investigation, InvestigationItem, InvestigationSubjectType } from "@/lib/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { ErrorNotice, SkeletonTable, StatusText } from "@/components/ui";

const SUBJECT_TYPES: readonly InvestigationSubjectType[] = ["finding", "anomaly", "transaction"];

export function InvestigationsPanel({ engagementId }: { engagementId: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const queryClient = useQueryClient();
  const toast = useToast();
  const listKey = ["investigations", engagementId];

  const { data, isLoading, error } = useQuery({
    queryKey: listKey,
    queryFn: () => bffGet<Investigation[]>(`/api/bff/engagements/${engagementId}/investigations`),
  });

  const create = useMutation({
    mutationFn: () =>
      bffPost<Investigation>(`/api/bff/engagements/${engagementId}/investigations`, {
        title,
        description,
      }),
    onSuccess: (investigation) => {
      toast.show(`Investigation "${investigation.title}" opened.`);
      setTitle("");
      setDescription("");
      void queryClient.invalidateQueries({ queryKey: listKey });
      setSelectedId(investigation.id);
    },
  });

  return (
    <div className="panel-stack">
      <section>
        <h2>Investigations</h2>
        <p className="lede mt-1">
          Open a case, pull the findings, anomalies, and transactions relevant to it into one
          working set, and close it with a documented conclusion when you&apos;re done — the same
          professional-judgment sign-off every disposition in this platform requires.
        </p>

        <div className="card mt-4 mb-4">
          <label className="kicker" htmlFor="investigation-title">
            Title
          </label>
          <input
            id="investigation-title"
            className="field mt-1.5"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <label className="kicker mt-3 block" htmlFor="investigation-description">
            Description
          </label>
          <textarea
            id="investigation-description"
            className="field mt-1.5 min-h-[70px]"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <div className="mt-2.5">
            <button
              className="btn btn-primary"
              onClick={() => create.mutate()}
              disabled={create.isPending || title.trim().length === 0 || description.trim().length === 0}
            >
              {create.isPending ? "Opening…" : "Open investigation"}
            </button>
          </div>
          {create.error ? (
            <div className="mt-3">
              <ErrorNotice error={create.error} />
            </div>
          ) : null}
        </div>

        {isLoading ? <SkeletonTable rows={3} cols={4} /> : null}
        {error ? (
          <div className="mt-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}
        {data && data.length === 0 ? <p className="muted">No investigations opened yet.</p> : null}
        {data && data.length > 0 ? (
          <table className="data">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Opened</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((investigation) => (
                <tr key={investigation.id}>
                  <td>{investigation.title}</td>
                  <td>
                    <StatusText status={investigation.status} />
                  </td>
                  <td className="muted">
                    {investigation.created_at ? new Date(investigation.created_at).toLocaleString() : "—"}
                  </td>
                  <td>
                    <button className="btn" onClick={() => setSelectedId(investigation.id)}>
                      Open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      {selectedId ? (
        <InvestigationDetail
          engagementId={engagementId}
          investigationId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      ) : null}
    </div>
  );
}

function InvestigationDetail({
  engagementId,
  investigationId,
  onClose,
}: {
  engagementId: string;
  investigationId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmAction = useConfirm();
  const investigationKey = ["investigation", engagementId, investigationId];
  const itemsKey = ["investigation-items", engagementId, investigationId];
  const listKey = ["investigations", engagementId];

  const {
    data: investigation,
    isLoading: investigationLoading,
    error: investigationError,
  } = useQuery({
    queryKey: investigationKey,
    queryFn: () =>
      bffGet<Investigation>(
        `/api/bff/engagements/${engagementId}/investigations/${investigationId}`,
      ),
  });

  const {
    data: items,
    isLoading: itemsLoading,
    error: itemsError,
  } = useQuery({
    queryKey: itemsKey,
    queryFn: () =>
      bffGet<InvestigationItem[]>(
        `/api/bff/engagements/${engagementId}/investigations/${investigationId}/items`,
      ),
  });

  const [subjectType, setSubjectType] = useState<InvestigationSubjectType>("finding");
  const [subjectId, setSubjectId] = useState("");
  const [note, setNote] = useState("");
  const [conclusion, setConclusion] = useState("");

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: itemsKey });
    void queryClient.invalidateQueries({ queryKey: investigationKey });
    void queryClient.invalidateQueries({ queryKey: listKey });
  };

  const addItem = useMutation({
    mutationFn: () =>
      bffPost<InvestigationItem>(
        `/api/bff/engagements/${engagementId}/investigations/${investigationId}/items`,
        { subject_type: subjectType, subject_id: subjectId, note: note.trim() || undefined },
      ),
    onSuccess: () => {
      toast.show("Added to working set.");
      setSubjectId("");
      setNote("");
      invalidateAll();
    },
  });

  const removeItem = useMutation({
    mutationFn: (itemId: string) =>
      bffDelete<void>(
        `/api/bff/engagements/${engagementId}/investigations/${investigationId}/items/${itemId}`,
      ),
    onSuccess: () => {
      toast.show("Removed from working set.");
      invalidateAll();
    },
  });

  async function confirmRemoveItem(itemId: string) {
    const ok = await confirmAction({
      title: "Remove this item from the working set?",
      description:
        "The underlying finding, anomaly, or transaction record itself is not affected — only its inclusion in this investigation.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok) removeItem.mutate(itemId);
  }

  const closeInvestigation = useMutation({
    mutationFn: () =>
      bffPost<Investigation>(
        `/api/bff/engagements/${engagementId}/investigations/${investigationId}/close`,
        { conclusion },
      ),
    onSuccess: () => {
      toast.show("Investigation closed.");
      invalidateAll();
    },
  });

  const isOpen = investigation?.status === "open";

  return (
    <section className="fade-in">
      <div className="panel-header-row">
        <h2>{investigation ? investigation.title : `Investigation ${investigationId.slice(0, 8)}…`}</h2>
        <button className="btn" onClick={onClose}>
          Close panel
        </button>
      </div>
      {investigationLoading ? <p className="muted mt-4">Loading investigation…</p> : null}
      {investigationError ? (
        <div className="mt-4">
          <ErrorNotice error={investigationError} />
        </div>
      ) : null}
      {investigation ? (
        <div className="mt-4 grid gap-1.5">
          <p className="lede">{investigation.description}</p>
          <p className="muted">
            Status: <StatusText status={investigation.status} />
          </p>
          {investigation.status === "closed" ? (
            <p className="muted">
              Closed {investigation.closed_at ? new Date(investigation.closed_at).toLocaleString() : ""}
              {investigation.conclusion ? ` — ${investigation.conclusion}` : ""}
            </p>
          ) : null}
        </div>
      ) : null}

      <h3 className="mt-6">Working set</h3>
      {itemsLoading ? <SkeletonTable rows={2} cols={3} /> : null}
      {itemsError ? (
        <div className="mt-1.5">
          <ErrorNotice error={itemsError} />
        </div>
      ) : null}
      {items && items.length === 0 ? (
        <p className="muted mt-1.5">No items added to this investigation yet.</p>
      ) : null}
      {items && items.length > 0 ? (
        <table className="data mt-1.5">
          <thead>
            <tr>
              <th>Type</th>
              <th>Subject</th>
              <th>Note</th>
              {isOpen ? <th></th> : null}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.subject_type}</td>
                <td className="mono">{item.subject_id.slice(0, 8)}…</td>
                <td className="muted">{item.note ?? "—"}</td>
                {isOpen ? (
                  <td>
                    <button
                      className="btn btn-danger"
                      onClick={() => void confirmRemoveItem(item.id)}
                      disabled={removeItem.isPending}
                    >
                      Remove
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {removeItem.error ? (
        <div className="mt-3">
          <ErrorNotice error={removeItem.error} />
        </div>
      ) : null}

      {isOpen ? (
        <div className="card my-4">
          <div className="kicker">Add item</div>
          <div className="mt-1.5 flex flex-wrap gap-2.5">
            <select
              className="field max-w-[160px]"
              value={subjectType}
              onChange={(e) => setSubjectType(e.target.value as InvestigationSubjectType)}
            >
              {SUBJECT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
            <input
              className="field min-w-[220px] flex-1"
              placeholder="Subject id"
              value={subjectId}
              onChange={(e) => setSubjectId(e.target.value)}
            />
          </div>
          <input
            className="field mt-2.5"
            placeholder="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="mt-2.5">
            <button
              className="btn"
              onClick={() => addItem.mutate()}
              disabled={addItem.isPending || subjectId.trim().length === 0}
            >
              {addItem.isPending ? "Adding…" : "Add to working set"}
            </button>
          </div>
          {addItem.error ? (
            <div className="mt-3">
              <ErrorNotice error={addItem.error} />
            </div>
          ) : null}
        </div>
      ) : null}

      {isOpen ? (
        <div className="card my-4">
          <div className="kicker">Close investigation</div>
          <textarea
            className="field mt-1.5 min-h-[70px]"
            placeholder="Conclusion (required)"
            value={conclusion}
            onChange={(e) => setConclusion(e.target.value)}
          />
          <div className="mt-2.5">
            <button
              className="btn btn-primary"
              onClick={() => closeInvestigation.mutate()}
              disabled={closeInvestigation.isPending || conclusion.trim().length === 0}
            >
              {closeInvestigation.isPending ? "Closing…" : "Close with conclusion"}
            </button>
          </div>
          {closeInvestigation.error ? (
            <div className="mt-3">
              <ErrorNotice error={closeInvestigation.error} />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
