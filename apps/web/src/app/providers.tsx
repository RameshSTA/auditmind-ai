"use client";

/**
 * TanStack Query provider — Phase 13 §11's decision that server state lives in the Query cache as
 * the single source of truth, never duplicated into a separate store. One client per browser tab,
 * created inside a state initializer so it survives Fast Refresh without being recreated per render.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ConfirmProvider } from "@/components/ConfirmDialog";
import { ToastProvider } from "@/components/Toast";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ConfirmProvider>{children}</ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
