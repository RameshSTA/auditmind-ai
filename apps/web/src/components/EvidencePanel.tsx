"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { bffGet, bffPost, bffUpload } from "@/lib/client-api";
import type { DocumentSummary, SearchResult } from "@/lib/types";
import { useToast } from "@/components/Toast";
import { ErrorNotice, SkeletonTable } from "@/components/ui";

export function EvidencePanel({ engagementId }: { engagementId: string }) {
  return (
    <div className="panel-stack">
      <DocumentsSection engagementId={engagementId} />
      <SearchSection engagementId={engagementId} />
    </div>
  );
}

function DocumentsSection({ engagementId }: { engagementId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<unknown>(null);

  const documentsKey = ["documents", engagementId];
  const { data, isLoading, error } = useQuery({
    queryKey: documentsKey,
    queryFn: () =>
      bffGet<DocumentSummary[]>(`/api/bff/engagements/${engagementId}/documents`),
  });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return bffUpload<DocumentSummary>(
        `/api/bff/engagements/${engagementId}/documents`,
        form,
      );
    },
    onSuccess: (doc) => {
      toast.show(`Uploaded "${doc.original_filename}".`);
      setUploadError(null);
      if (fileInput.current) fileInput.current.value = "";
      void queryClient.invalidateQueries({ queryKey: documentsKey });
    },
    onError: (err) => setUploadError(err),
  });

  return (
    <section>
      <h2>Documents</h2>
      <p className="lede mt-1">Evidence uploaded to this engagement.</p>

      <div className="card mt-4 mb-4">
        <label className="kicker" htmlFor="doc-upload">
          Upload document
        </label>
        <div className="mt-1.5 flex flex-wrap gap-2.5">
          <input id="doc-upload" ref={fileInput} type="file" className="field max-w-[360px]" />
          <button
            className="btn btn-primary"
            disabled={upload.isPending}
            onClick={() => {
              const file = fileInput.current?.files?.[0];
              if (file) upload.mutate(file);
            }}
          >
            {upload.isPending ? "Uploading…" : "Upload"}
          </button>
        </div>
        {uploadError ? (
          <div className="mt-3">
            <ErrorNotice error={uploadError} />
          </div>
        ) : null}
      </div>

      {isLoading ? <SkeletonTable rows={3} cols={5} /> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <p className="muted">No documents yet. Upload one above.</p>
      ) : null}
      {data && data.length > 0 ? (
        <table className="data">
          <thead>
            <tr>
              <th>Filename</th>
              <th>Type</th>
              <th>Status</th>
              <th>Note</th>
              <th>Semantic search</th>
            </tr>
          </thead>
          <tbody>
            {data.map((doc) => (
              <tr key={doc.id}>
                <td>{doc.original_filename}</td>
                <td className="mono">{doc.mime_type}</td>
                <td>{doc.status}</td>
                <td className="muted">
                  {doc.duplicate_of ? "Duplicate of an existing document (deduped by hash)" : "—"}
                </td>
                <td>
                  <EmbedAction engagementId={engagementId} documentId={doc.id} status={doc.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function EmbedAction({
  engagementId,
  documentId,
  status,
}: {
  engagementId: string;
  documentId: string;
  status: string;
}) {
  const toast = useToast();
  const embed = useMutation({
    mutationFn: () =>
      bffPost<{ document_id: string; embedded_count: number }>(
        `/api/bff/engagements/${engagementId}/documents/${documentId}/embeddings`,
      ),
    onSuccess: (result) => toast.show(`Embedded ${result.embedded_count} chunk(s).`),
  });

  if (status !== "parsed") {
    return <span className="muted">Waiting on parsing…</span>;
  }
  if (embed.isSuccess) {
    return <span className="muted">Embedded {embed.data.embedded_count} chunk(s)</span>;
  }
  return (
    <div>
      <button className="btn" onClick={() => embed.mutate()} disabled={embed.isPending}>
        {embed.isPending ? "Embedding…" : "Generate embeddings"}
      </button>
      {embed.error ? (
        <div className="mt-1.5">
          <ErrorNotice error={embed.error} />
        </div>
      ) : null}
    </div>
  );
}

function SearchSection({ engagementId }: { engagementId: string }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"keyword" | "semantic">("keyword");
  const [submitted, setSubmitted] = useState<{ q: string; mode: string } | null>(null);

  const { data, isFetching, error } = useQuery({
    queryKey: ["search", engagementId, submitted?.q, submitted?.mode],
    queryFn: () =>
      bffGet<SearchResult[]>(
        `/api/bff/engagements/${engagementId}/search?q=${encodeURIComponent(
          submitted!.q,
        )}&mode=${submitted!.mode}`,
      ),
    enabled: submitted !== null && submitted.q.trim().length > 0,
  });

  return (
    <section>
      <h2>Search evidence</h2>
      <p className="lede mt-1">
        Keyword or semantic (vector similarity) search across this engagement&apos;s evidence.
        Semantic search only returns
        chunks that have been embedded.
      </p>
      <form
        className="my-4 flex flex-wrap gap-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted({ q: query, mode });
        }}
      >
        <input
          className="field max-w-[420px]"
          placeholder="Search this engagement's evidence…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search query"
        />
        <select
          className="field max-w-[150px]"
          value={mode}
          onChange={(e) => setMode(e.target.value === "semantic" ? "semantic" : "keyword")}
          aria-label="Search mode"
        >
          <option value="keyword">Keyword</option>
          <option value="semantic">Semantic</option>
        </select>
        <button className="btn btn-primary" type="submit">
          Search
        </button>
      </form>

      {isFetching ? <p className="muted">Searching…</p> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {submitted && data && data.length === 0 && !isFetching ? (
        <div className="notice">
          No matching evidence for &quot;{submitted.q}&quot;. For semantic search, remember chunks
          must be embedded first.
        </div>
      ) : null}
      {data && data.length > 0 ? (
        <div className="grid gap-2.5">
          {data.map((r) => (
            <div key={r.chunk_id} className="card">
              <div className="kicker">
                doc {r.document_id.slice(0, 8)}… · rank {r.rank.toFixed(4)}
              </div>
              <p className="mt-1.5">{r.text}</p>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
