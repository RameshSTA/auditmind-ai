"use client";

/**
 * Knowledge Graph — vendor list resolved from this engagement's transactions (Increment 09), plus
 * a vendor-360 drill-down (every transaction paid to one vendor). Reads Neo4j through the API's
 * knowledge_graph endpoints; nothing here is a mock graph visualization standing in for real data.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { bffGet, bffPost } from "@/lib/client-api";
import type { Vendor, VendorNetwork } from "@/lib/types";
import { useToast } from "@/components/Toast";
import { ErrorNotice, SkeletonTable } from "@/components/ui";

export function KnowledgeGraphPanel({ engagementId }: { engagementId: string }) {
  const [selectedVendorId, setSelectedVendorId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();
  const vendorsKey = ["kg-vendors", engagementId];

  const { data, isLoading, error } = useQuery({
    queryKey: vendorsKey,
    queryFn: () => bffGet<Vendor[]>(`/api/bff/engagements/${engagementId}/knowledge-graph/vendors`),
  });

  const resolve = useMutation({
    mutationFn: () =>
      bffPost<{ newly_resolved_count: number }>(
        `/api/bff/engagements/${engagementId}/knowledge-graph/resolve`,
      ),
    onSuccess: (result) => {
      toast.show(`Resolved ${result.newly_resolved_count} new vendor(s).`);
      void queryClient.invalidateQueries({ queryKey: vendorsKey });
    },
  });

  return (
    <div className="panel-stack">
      <section>
        <div className="panel-header-row">
          <h2>Vendors</h2>
          <button className="btn" onClick={() => resolve.mutate()} disabled={resolve.isPending}>
            {resolve.isPending ? "Resolving…" : "Resolve vendors"}
          </button>
        </div>
        <p className="lede mt-1">
          Reads this engagement&apos;s transactions, groups them by normalized vendor name, and
          upserts Vendor/Transaction nodes into the graph (Neo4j) — the read model behind the
          graph-centrality signal in Risk &amp; Anomalies. Idempotent and self-healing: safe to
          call again after importing more transactions.
        </p>
        {resolve.error ? (
          <div className="mt-3">
            <ErrorNotice error={resolve.error} />
          </div>
        ) : null}

        {isLoading ? <SkeletonTable rows={3} cols={4} /> : null}
        {error ? (
          <div className="mt-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}
        {data && data.length === 0 ? (
          <p className="muted mt-4">
            No vendors resolved yet. Import transactions on the Risk &amp; Anomalies tab, then
            resolve vendors here.
          </p>
        ) : null}
        {data && data.length > 0 ? (
          <table className="data mt-4">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Transactions</th>
                <th>Total</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((vendor) => (
                <tr key={vendor.id}>
                  <td>{vendor.name}</td>
                  <td className="mono">{vendor.transaction_count}</td>
                  <td className="mono">
                    {Object.entries(vendor.total_amount_by_currency)
                      .map(([currency, amount]) => `${amount} ${currency}`)
                      .join(", ") || "—"}
                  </td>
                  <td>
                    <button className="btn" onClick={() => setSelectedVendorId(vendor.id)}>
                      View network
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      {selectedVendorId ? (
        <VendorNetworkSection
          engagementId={engagementId}
          vendorId={selectedVendorId}
          onClose={() => setSelectedVendorId(null)}
        />
      ) : null}
    </div>
  );
}

function VendorNetworkSection({
  engagementId,
  vendorId,
  onClose,
}: {
  engagementId: string;
  vendorId: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["kg-vendor", engagementId, vendorId],
    queryFn: () =>
      bffGet<VendorNetwork>(
        `/api/bff/engagements/${engagementId}/knowledge-graph/vendors/${vendorId}`,
      ),
  });

  return (
    <section className="fade-in">
      <div className="panel-header-row">
        <h2>{data ? data.name : "Vendor"} — network</h2>
        <button className="btn" onClick={onClose}>
          Close
        </button>
      </div>
      {isLoading ? <p className="muted mt-4">Loading vendor network…</p> : null}
      {error ? (
        <div className="mt-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}
      {data && data.transactions.length === 0 ? (
        <p className="muted mt-4">No transactions on record for this vendor.</p>
      ) : null}
      {data && data.transactions.length > 0 ? (
        <table className="data mt-4">
          <thead>
            <tr>
              <th>Transaction</th>
              <th>Amount</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            {data.transactions.map((t) => (
              <tr key={t.transaction_id}>
                <td className="mono">{t.transaction_id.slice(0, 8)}…</td>
                <td className="mono">
                  {t.amount} {t.currency}
                </td>
                <td className="muted">{t.transaction_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
