"use client";

/**
 * Monitoring — real liveness/readiness of both backend services (apps/api and
 * agent-orchestrator), checked live, plus real links out to Grafana and Prometheus.
 * Deliberately does not embed Grafana panels or scrape Prometheus metrics into this page: an
 * iframe embed or a hand-rolled metrics widget would either need Grafana anonymous-embed auth
 * wired up (not done) or duplicate a real dashboard that already exists — this links to the real
 * thing instead of building a worse copy of it.
 *
 * The platform-status banner and "last checked" timestamps are computed client-side from this
 * same poll (`dataUpdatedAt` is TanStack Query's own record of when the last successful fetch
 * landed) — nothing here is a fabricated uptime percentage or a history this page never measured.
 */
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BarChart3, CheckCircle2, Gauge, Server, XCircle } from "lucide-react";

import { bffGet } from "@/lib/client-api";
import { ErrorNotice, SkeletonCards } from "@/components/ui";

interface ServiceHealth {
  name: string;
  healthy: boolean;
  ready: boolean;
  error: string | null;
}

interface SystemHealth {
  services: ServiceHealth[];
}

const SERVICE_LABELS: Record<string, string> = {
  api: "API",
  "agent-orchestrator": "Agent Orchestrator",
};

const SERVICE_DESCRIPTIONS: Record<string, string> = {
  api: "Core backend — identity, ingestion, risk, reporting, retrieval, knowledge graph.",
  "agent-orchestrator": "The 9-agent LangGraph runtime behind AI Copilot.",
};

const GRAFANA_URL = "http://localhost:3001/d/auditmind-platform-health";
const PROMETHEUS_URL = "http://localhost:9090";

export function MonitoringPanel() {
  const { data, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ["system-health"],
    queryFn: () => bffGet<SystemHealth>("/api/bff/system/health"),
    refetchInterval: 15000,
  });

  const allOperational = data?.services.every((s) => s.healthy && s.ready) ?? null;
  const downCount = data?.services.filter((s) => !s.healthy || !s.ready).length ?? 0;

  return (
    <div className="panel-stack">
      <section>
        <h2>Service health</h2>
        <p className="lede mt-1">
          Live liveness (<span className="mono">/healthz</span>) and readiness (
          <span className="mono">/readyz</span>) checks, re-run every 15 seconds — not a cached or
          assumed status.
        </p>

        {isLoading ? <SkeletonCards count={2} /> : null}
        {error ? (
          <div className="mt-4">
            <ErrorNotice error={error} />
          </div>
        ) : null}

        {data ? (
          <>
            <div className={`status-banner mt-4 ${allOperational ? "status-banner-ok" : "status-banner-down"}`}>
              {allOperational ? (
                <CheckCircle2 size={18} strokeWidth={2} aria-hidden="true" />
              ) : (
                <AlertTriangle size={18} strokeWidth={2} aria-hidden="true" />
              )}
              <span className="font-medium">
                {allOperational
                  ? "All systems operational"
                  : `${downCount} of ${data.services.length} service(s) need attention`}
              </span>
              {dataUpdatedAt ? (
                <span className="mono ml-auto text-xs opacity-75">
                  Checked {new Date(dataUpdatedAt).toLocaleTimeString()}
                </span>
              ) : null}
            </div>

            <div className="mt-3 grid gap-3">
              {data.services.map((service) => {
                const operational = service.healthy && service.ready;
                return (
                  <div key={service.name} className="card service-card">
                    <div className="flex items-start gap-3">
                      <span className={`service-icon ${operational ? "service-icon-ok" : "service-icon-down"}`}>
                        <Server size={16} strokeWidth={1.75} aria-hidden="true" />
                      </span>
                      <div>
                        <div className="font-semibold text-ink">{SERVICE_LABELS[service.name] ?? service.name}</div>
                        <div className="muted mt-0.5 text-[13px]">
                          {SERVICE_DESCRIPTIONS[service.name] ?? "—"}
                        </div>
                      </div>
                    </div>
                    <div className="service-card-status">
                      <span className={service.healthy ? "status-completed" : "status-failed"}>
                        {service.healthy ? (
                          <CheckCircle2 size={13} strokeWidth={2} className="mr-1 inline" aria-hidden="true" />
                        ) : (
                          <XCircle size={13} strokeWidth={2} className="mr-1 inline" aria-hidden="true" />
                        )}
                        {service.healthy ? "Healthy" : "Down"}
                      </span>
                      <span className={service.ready ? "status-completed" : "status-failed"}>
                        {service.ready ? "Ready" : "Not ready"}
                      </span>
                      {service.error ? <span className="muted text-xs">{service.error}</span> : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : null}
      </section>

      <section>
        <h2>Dashboards &amp; metrics</h2>
        <p className="lede mt-1">
          Full request/trace/metric detail lives in Grafana and Prometheus — real tools, not
          reimplemented here.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <a className="card service-link-card" href={GRAFANA_URL} target="_blank" rel="noopener noreferrer">
            <span className="service-icon service-icon-neutral">
              <BarChart3 size={16} strokeWidth={1.75} aria-hidden="true" />
            </span>
            <div>
              <div className="font-semibold text-ink">Grafana</div>
              <div className="muted text-[13px]">Traces, dashboards, and service metrics.</div>
            </div>
          </a>
          <a className="card service-link-card" href={PROMETHEUS_URL} target="_blank" rel="noopener noreferrer">
            <span className="service-icon service-icon-neutral">
              <Gauge size={16} strokeWidth={1.75} aria-hidden="true" />
            </span>
            <div>
              <div className="font-semibold text-ink">Prometheus</div>
              <div className="muted text-[13px]">Raw metrics and alerting rules.</div>
            </div>
          </a>
        </div>
      </section>
    </div>
  );
}
