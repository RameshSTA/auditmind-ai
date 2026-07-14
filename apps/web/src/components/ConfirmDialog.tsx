"use client";

/**
 * Promise-based confirm dialog for the platform's genuinely destructive, no-undo actions —
 * reserved for cases with no other friction already gating them (e.g. Investigations' working-set
 * "Remove", InvestigationsPanel.tsx). Most mutations here already require a typed reason first
 * (finding rejection, HITL reject/edit) — that's confirmation enough, so this isn't wired to those.
 */
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type ConfirmOptions = {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

type PendingConfirm = ConfirmOptions & { resolve: (value: boolean) => void };

const ConfirmContext = createContext<((options: ConfirmOptions) => Promise<boolean>) | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const pendingRef = useRef<PendingConfirm | null>(null);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      const next = { ...options, resolve };
      pendingRef.current = next;
      setPending(next);
    });
  }, []);

  const settle = (value: boolean) => {
    pendingRef.current?.resolve(value);
    pendingRef.current = null;
    setPending(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending ? (
        <div className="dialog-overlay" onClick={() => settle(false)}>
          <div
            className="dialog fade-in"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") settle(false);
            }}
          >
            <h3 id="confirm-dialog-title">{pending.title}</h3>
            {pending.description ? <p className="muted mt-1.5">{pending.description}</p> : null}
            <div className="mt-4 flex justify-end gap-2.5">
              {/* eslint-disable-next-line jsx-a11y/no-autofocus -- keyboard-first: land on the
                  non-destructive choice so Enter never confirms a destructive action by accident. */}
              <button type="button" className="btn" onClick={() => settle(false)} autoFocus>
                {pending.cancelLabel ?? "Cancel"}
              </button>
              <button
                type="button"
                className={pending.danger ? "btn btn-danger" : "btn btn-primary"}
                onClick={() => settle(true)}
              >
                {pending.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within ConfirmProvider");
  return ctx;
}
