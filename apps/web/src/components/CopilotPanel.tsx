"use client";

/**
 * AI Copilot — a real conversation, backed by agent-orchestrator's chat router
 * (``CopilotChatService``): every message either answers directly (grounded in a hand-authored,
 * accurate description of what this platform actually has today — never the aspirational design
 * docs), invokes one real direct action (import transactions, run the anomaly scan, compute risk
 * scores, resolve vendors, generate a report, generate embeddings — confirmed against the real
 * result, never a fabricated "done"), or hands off to the existing, unmodified investigation
 * pipeline (planner → evidence specialists → evaluation → guardrail → human sign-off) for anything
 * that needs real evidence-gathering or could result in a drafted finding. Nothing here
 * reimplements that pipeline — a "run reference" message is a thin pointer at it, with the same
 * real HITL approve/edit/reject affordance the old task-form UI had, now inline in the thread.
 *
 * No token streaming: agent-orchestrator's LLM client is request/response, not SSE, so this shows
 * a real "Thinking…" indicator and reveals the full response on arrival — an honest non-streaming
 * UX, not a fake typing effect.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { Bot, Paperclip, Send, User } from "lucide-react";

import { bffGet, bffPost, bffUpload } from "@/lib/client-api";
import type { CopilotMessage, DocumentSummary, HitlDecision } from "@/lib/types";
import { useToast } from "@/components/Toast";
import { ErrorNotice, Skeleton } from "@/components/ui";

const DECISION_LABEL: Record<HitlDecision, string> = {
  approve: "approved",
  edit: "edited",
  reject: "rejected",
};

export function CopilotPanel({ engagementId }: { engagementId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const messagesKey = ["copilot-messages", engagementId];
  const [draft, setDraft] = useState("");
  const scrollAnchorRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: messagesKey,
    queryFn: () =>
      bffGet<CopilotMessage[]>(`/api/bff/engagements/${engagementId}/copilot/messages`),
  });

  const sendMessage = useMutation({
    mutationFn: (content: string) =>
      bffPost<CopilotMessage[]>(`/api/bff/engagements/${engagementId}/copilot/messages`, {
        content,
      }),
    onSuccess: () => {
      setDraft("");
      void queryClient.invalidateQueries({ queryKey: messagesKey });
    },
  });

  // Document upload isn't an LLM tool call — an LLM can't attach binary bytes to a JSON tool
  // call — it's a real file upload through the same endpoint EvidencePanel's Documents page uses,
  // then a chat message naming the real resulting document so the assistant can act on it (e.g.
  // generate embeddings for it) with a real id, not a guessed one.
  const uploadDocument = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return bffUpload<DocumentSummary>(`/api/bff/engagements/${engagementId}/documents`, form);
    },
    onSuccess: (doc) => {
      if (fileInputRef.current) fileInputRef.current.value = "";
      toast.show(`Uploaded "${doc.original_filename}".`);
      sendMessage.mutate(
        `I uploaded a document: "${doc.original_filename}" (document_id: ${doc.id}).`,
      );
    },
  });

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [data?.length, sendMessage.isPending, uploadDocument.isPending]);

  function handleSend() {
    const content = draft.trim();
    if (!content || sendMessage.isPending) return;
    sendMessage.mutate(content);
  }

  return (
    <div className="panel-stack">
      <section>
        <h2>AI Copilot</h2>
        <p className="lede mt-1">
          Ask how something works, ask a question grounded in this engagement&apos;s real
          evidence, or ask it to do something — import transactions, run the anomaly scan,
          resolve vendors, generate a report — and it will answer, act, or start a real
          investigation that stops for your sign-off before anything is published.
        </p>

        <div className="copilot-thread mt-4">
          {isLoading ? (
            <div className="grid gap-3">
              <Skeleton className="h-12 w-2/3" />
              <Skeleton className="ml-auto h-12 w-1/2" />
            </div>
          ) : null}
          {error ? <ErrorNotice error={error} /> : null}
          {data && data.length === 0 ? (
            <div className="copilot-empty">
              <Bot size={28} strokeWidth={1.5} aria-hidden="true" />
              <p className="muted mt-2">
                Try &quot;How do I generate a report?&quot; or &quot;Run the anomaly scan.&quot;
              </p>
            </div>
          ) : null}
          {data?.map((message, index) => (
            <CopilotMessageBubble
              key={message.id}
              message={message}
              engagementId={engagementId}
              messagesKey={messagesKey}
              isResolved={data
                .slice(index + 1)
                .some((later) => later.run_id !== null && later.run_id === message.run_id)}
            />
          ))}
          {sendMessage.isPending ? (
            <div className="copilot-message copilot-message-assistant">
              <Bot size={16} strokeWidth={1.75} aria-hidden="true" className="copilot-avatar" />
              <div className="copilot-bubble">
                <span className="copilot-thinking">Thinking…</span>
              </div>
            </div>
          ) : null}
          <div ref={scrollAnchorRef} />
        </div>

        {sendMessage.error ? (
          <div className="mt-3">
            <ErrorNotice error={sendMessage.error} />
          </div>
        ) : null}
        {uploadDocument.error ? (
          <div className="mt-3">
            <ErrorNotice error={uploadDocument.error} />
          </div>
        ) : null}

        <form
          className="copilot-composer mt-3"
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            aria-label="Attach a document"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadDocument.mutate(file);
            }}
          />
          <button
            type="button"
            className="btn copilot-attach-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadDocument.isPending}
            aria-label="Attach a document"
            title="Attach a document"
          >
            {uploadDocument.isPending ? (
              <span className="copilot-thinking">Uploading…</span>
            ) : (
              <Paperclip size={15} strokeWidth={1.75} aria-hidden="true" />
            )}
          </button>
          <textarea
            className="field copilot-composer-input"
            placeholder="Message AI Copilot…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            rows={2}
            aria-label="Message AI Copilot"
          />
          <button
            className="btn btn-primary"
            type="submit"
            disabled={sendMessage.isPending || draft.trim().length === 0}
            aria-label="Send"
          >
            <Send size={15} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </form>
      </section>
    </div>
  );
}

function CopilotMessageBubble({
  message,
  engagementId,
  messagesKey,
  isResolved,
}: {
  message: CopilotMessage;
  engagementId: string;
  messagesKey: string[];
  isResolved: boolean;
}) {
  const isUser = message.role === "user";
  const awaitingReview =
    message.message_type === "run_reference" &&
    message.run_id !== null &&
    message.metadata?.run_status === "awaiting_human_review" &&
    !isResolved;

  return (
    <div
      className={`copilot-message ${isUser ? "copilot-message-user" : "copilot-message-assistant"}`}
    >
      {!isUser ? (
        <Bot size={16} strokeWidth={1.75} aria-hidden="true" className="copilot-avatar" />
      ) : null}
      <div className="copilot-bubble">
        {message.message_type === "action_result" ? (
          <div className="kicker mb-1">Action</div>
        ) : null}
        <ReactMarkdown>{message.content}</ReactMarkdown>
        {awaitingReview ? (
          <CopilotCheckpointCard
            engagementId={engagementId}
            message={message}
            messagesKey={messagesKey}
          />
        ) : null}
      </div>
      {isUser ? (
        <User size={16} strokeWidth={1.75} aria-hidden="true" className="copilot-avatar" />
      ) : null}
    </div>
  );
}

function CopilotCheckpointCard({
  engagementId,
  message,
  messagesKey,
}: {
  engagementId: string;
  message: CopilotMessage;
  messagesKey: string[];
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");

  const resolve = useMutation({
    mutationFn: (decision: HitlDecision) =>
      bffPost<CopilotMessage>(
        `/api/bff/engagements/${engagementId}/copilot/messages/${message.id}/resolve`,
        { decision, reason: reason.trim() || undefined },
      ),
    onSuccess: (_result, decision) => {
      toast.show(`Checkpoint ${DECISION_LABEL[decision]}.`);
      void queryClient.invalidateQueries({ queryKey: messagesKey });
    },
  });

  return (
    <div className="notice warn copilot-checkpoint mt-3">
      <div className="mb-2">Waiting on your review before anything is published.</div>
      <input
        className="field"
        placeholder="Reason (required for reject/edit)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        aria-label="Reason"
      />
      <div className="mt-2.5 flex flex-wrap gap-2.5">
        <button
          className="btn btn-primary"
          onClick={() => resolve.mutate("approve")}
          disabled={resolve.isPending}
        >
          Approve
        </button>
        <button
          className="btn"
          onClick={() => resolve.mutate("edit")}
          disabled={resolve.isPending || reason.trim().length === 0}
        >
          Edit
        </button>
        <button
          className="btn btn-danger"
          onClick={() => resolve.mutate("reject")}
          disabled={resolve.isPending || reason.trim().length === 0}
        >
          Reject
        </button>
      </div>
      {resolve.error ? (
        <div className="mt-3">
          <ErrorNotice error={resolve.error} />
        </div>
      ) : null}
    </div>
  );
}
