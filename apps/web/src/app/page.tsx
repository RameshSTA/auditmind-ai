import Link from "next/link";
import { redirect } from "next/navigation";

import { LogoMark } from "@/components/Logo";
import { getSessionPersona } from "@/server/session";

const CAPABILITIES = [
  {
    title: "Evidence search",
    body: "Keyword and semantic search across every document in an engagement, with every result citing its source chunk.",
  },
  {
    title: "Risk & fraud detection",
    body: "Rule-based anomaly detection plus a machine-learning ensemble (Isolation Forest, cohort clustering, graph centrality) scores every transaction, with a full explainability breakdown behind each score.",
  },
  {
    title: "Knowledge graph",
    body: "Vendor relationships and payment networks resolved from transaction data, so a fraud investigation can follow the money, not just a spreadsheet row.",
  },
  {
    title: "AI Copilot",
    body: "An agentic assistant that drafts findings from real evidence — every draft stops at a human-in-the-loop checkpoint before anything is published.",
  },
  {
    title: "Findings & reporting",
    body: "Every finding passes a mandatory human sign-off gate before it can appear in a report. Reports are immutable and versioned.",
  },
  {
    title: "Access control built in",
    body: "Role-based permissions and database-level row security mean an engagement's data is only ever visible to people on that engagement — enforced on every request, not just in the UI.",
  },
];

export default async function LandingPage() {
  const persona = await getSessionPersona();
  if (persona) redirect("/engagements");

  return (
    <div className="mx-auto max-w-3xl">
      <section className="py-10 text-center">
        <div
          className="fade-in-up mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-line-strong"
          style={{ "--stagger": "0ms" } as React.CSSProperties}
        >
          <LogoMark size={28} />
        </div>
        <p className="fade-in-up kicker mt-4" style={{ "--stagger": "60ms" } as React.CSSProperties}>
          Audit intelligence platform
        </p>
        <h1
          className="fade-in-up mt-2 text-3xl"
          style={{ "--stagger": "120ms" } as React.CSSProperties}
        >
          AuditMind AI
        </h1>
        <p
          className="lede fade-in-up mx-auto mt-3 max-w-[56ch] text-base"
          style={{ "--stagger": "180ms" } as React.CSSProperties}
        >
          Full-population evidence review, AI-assisted risk scoring, and fraud detection — with
          every finding traceable back to its source evidence and every AI draft signed off by a
          human before it counts.
        </p>
        <div
          className="fade-in-up mt-6 flex items-center justify-center gap-3"
          style={{ "--stagger": "240ms" } as React.CSSProperties}
        >
          <Link href="/login?intent=create" className="btn btn-primary px-6 py-2.5 text-sm">
            Create account
          </Link>
          <Link href="/login" className="btn px-6 py-2.5 text-sm">
            Sign in
          </Link>
        </div>
      </section>

      <section className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {CAPABILITIES.map((c, i) => (
          <div
            key={c.title}
            className="card fade-in-up"
            style={{ "--stagger": `${300 + i * 60}ms` } as React.CSSProperties}
          >
            <div className="font-semibold">{c.title}</div>
            <p className="muted mt-1.5">{c.body}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
