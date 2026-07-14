"use client";

/**
 * Ephemeral action-feedback toasts — confirms a mutation actually landed (create/confirm/reject/
 * generate/etc.) beyond the list silently re-rendering. Errors stay in ErrorNotice, not here: a
 * toast that auto-dismisses would carry away the trace_id a user needs to quote to support, so
 * this is deliberately for success confirmation, with an "error" variant reserved for feedback
 * about the toast-worthy action itself failing outside a form (never a replacement for ErrorNotice).
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type ToastVariant = "success" | "error";
type ToastRecord = { id: number; message: string; variant: ToastVariant };

type ToastContextValue = {
  show: (message: string, variant?: ToastVariant) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);
const DISMISS_AFTER_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, variant: ToastVariant = "success") => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, variant }]);
      window.setTimeout(() => dismiss(id), DISMISS_AFTER_MS);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.variant === "error" ? " toast-error" : ""} fade-in`}>
            <span>{t.message}</span>
            <button type="button" className="toast-dismiss" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
