"""AI Copilot's product-help grounding — a short, hand-authored, accurate description of what
AuditMind AI actually has today, injected into the chat router's system prompt.

Deliberately not built from design docs, which describe intent rather than shipped behavior. A
"how do I do X" answer needs precision (which page, which button, which role) more than semantic
recall over long design prose, so a curated reference — kept in sync by hand as real features ship
— is the honest choice here, the same way a real product's in-app help widget is usually
hand-authored rather than RAG'd over its own spec documents.
"""

from __future__ import annotations

PLATFORM_REFERENCE = """\
AuditMind AI is a real, working internal-audit platform — every page below is built and backed by \
real data, not a mockup. Everything is scoped to one engagement (one audit) at a time.

Pages and what they actually do:
- Documents & Evidence: upload a document (any engagement member can), generate semantic \
embeddings for it, then keyword or semantic search across all uploaded evidence.
- Findings: draft a finding (Auditor, FraudAnalyst, or ComplianceManager can draft); an Auditor or \
FraudAnalyst must then confirm or reject it — every finding requires this human sign-off before it \
counts for anything.
- Risk & Anomalies: paste/import transactions, run the rule-engine anomaly scan (Benford's Law \
deviation, duplicate-payment matching, threshold/round-dollar detection), compute the ML risk \
score ensemble (Isolation Forest + HDBSCAN cohort clustering + graph centrality), and confirm or \
dismiss each flagged anomaly.
- Fraud Detection: the same transactions, sorted by computed risk score, highest first — the \
triage view for "what should I look at."
- Investigations: open a case, add findings/anomalies/transactions to its working set, close it \
with a documented conclusion.
- Knowledge Graph: resolve vendor relationships from transaction data, then view any one vendor's \
full transaction network.
- Reports: generate a new versioned report compiling every currently-confirmed finding, with real \
cited evidence (or an honest "none attached" — never a fabricated citation); download it as a real \
PDF or print it.
- Evaluation: real aggregate stats — run outcomes and human sign-off decisions — over this \
engagement's agent activity.
- Administration: read-only roster of who is on this engagement and in what role.
- Monitoring: live health of the backend services.

Roles: Auditor and FraudAnalyst can author and disposition findings/anomalies and run every data \
action. ComplianceManager can author findings/import data but not disposition (confirm/reject/ \
dismiss). CAE is read-only oversight across all of the above.

If someone asks how to do something, name the exact page and button — precision beats a long \
explanation. If they ask you to actually do it and it's something you have a direct action or a \
real investigation for, prefer doing it over just describing the steps.\
"""
