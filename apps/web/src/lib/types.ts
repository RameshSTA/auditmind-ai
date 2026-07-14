/**
 * Response types mirroring the FastAPI backend's hand-written response builders in
 * apps/api/src/auditmind_api/main.py (`_finding_response`, `_anomaly_response`, etc.).
 *
 * Phase 13 §12's stated end state is a client TYPE-GENERATED from the OpenAPI spec, so a backend
 * contract change becomes a compile error here. That generator is deferred this increment (see the
 * increment doc's "what's deferred") — these are hand-written to match the current shapes exactly.
 * They live in one file so the eventual swap to generated types is a single-file replacement, and
 * so any drift shows up as a type error in the BFF handlers that consume them, not at runtime in
 * front of a user.
 */

export interface Membership {
  engagement_id: string;
  name: string;
  role: string;
}

/** Mirrors `AuthIdentityResponse` (identity/interface/schemas.py) — the shape both
 * `POST /v1/auth/register` and `POST /v1/auth/login` return. */
export interface AuthIdentity {
  subject: string;
  display_name: string;
  email: string;
  engagement_id: string | null;
  role: string | null;
}

/** The professional roles a self-service signup may choose from — Admin is excluded (must be
 * granted by an existing administrator, never self-claimed). Mirrors `SELF_SERVICE_ROLES`
 * (identity/application/services.py). */
export const SIGNUP_ROLES: ReadonlyArray<{ value: string; label: string; description: string }> = [
  {
    value: "Auditor",
    label: "Auditor",
    description: "Evidence review, control testing, and findings sign-off.",
  },
  {
    value: "FraudAnalyst",
    label: "Fraud & Forensic Analyst",
    description: "Anomaly triage, investigations, and findings sign-off.",
  },
  {
    value: "ComplianceManager",
    label: "Compliance & Controls Manager",
    description: "Drafts findings; sign-off requires an Auditor or Fraud Analyst.",
  },
  {
    value: "CAE",
    label: "Chief Audit Executive",
    description: "Portfolio-wide, read-only oversight.",
  },
];

export type FindingSeverity = "critical" | "high" | "medium" | "low";
export type FindingStatus = "draft" | "confirmed" | "rejected";

export interface Finding {
  id: string;
  engagement_id: string;
  control_id: string | null;
  title: string;
  description: string;
  severity: FindingSeverity;
  status: FindingStatus;
  created_by: string;
  disposition_reason: string | null;
  reviewed_by: string | null;
}

export interface FindingEvidence {
  id: string;
  chunk_id: string;
  citation_text: string;
}

export interface DocumentSummary {
  id: string;
  original_filename: string;
  status: string;
  mime_type: string;
  duplicate_of: string | null;
}

export interface SearchResult {
  chunk_id: string;
  document_id: string;
  text: string;
  rank: number;
}

export type AnomalyStatus = "open" | "true_positive" | "false_positive";

export interface Anomaly {
  id: string;
  engagement_id: string;
  anomaly_type: string;
  severity: FindingSeverity;
  status: AnomalyStatus;
  transaction_id: string | null;
  details: Record<string, unknown>;
  reviewed_by: string | null;
}

export interface RiskScore {
  id: string;
  engagement_id: string;
  subject_type: string;
  subject_id: string;
  score: string;
  score_version: string;
  contributing_factors: Record<string, unknown>;
  computed_at: string;
}

export interface TransactionRecord {
  id: string;
  engagement_id: string;
  source_system: string;
  amount: string;
  currency: string;
  transaction_date: string;
}

export interface Report {
  id: string;
  engagement_id: string;
  version: number;
  generated_by: string;
  generated_at: string;
  finding_ids: string[];
  body_markdown: string;
  exported_uri: string | null;
}

export interface Vendor {
  id: string;
  name: string;
  normalized_name: string;
  transaction_count: number;
  total_amount_by_currency: Record<string, string>;
}

export interface VendorTransaction {
  transaction_id: string;
  amount: string;
  currency: string;
  transaction_date: string;
}

export interface VendorNetwork extends Vendor {
  transactions: VendorTransaction[];
}

export type HitlDecision = "approve" | "reject" | "edit";

export interface HitlInterrupt {
  id: string;
  run_id: string;
  engagement_id: string;
  step_name: string;
  decision: HitlDecision | null;
  reviewer_id: string | null;
  reason: string | null;
  created_at: string | null;
  resolved_at: string | null;
}

export type MessageRole = "user" | "assistant";
export type MessageType = "text" | "action_result" | "run_reference";

export interface CopilotMessage {
  id: string;
  engagement_id: string;
  user_id: string;
  role: MessageRole;
  message_type: MessageType;
  content: string;
  run_id: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

export interface EngagementRosterEntry {
  user_id: string;
  display_name: string;
  email: string;
  role: string;
  granted_at: string;
}

export type InvestigationStatus = "open" | "closed";
export type InvestigationSubjectType = "finding" | "anomaly" | "transaction";

export interface Investigation {
  id: string;
  engagement_id: string;
  title: string;
  description: string;
  status: InvestigationStatus;
  created_by: string;
  created_at: string;
  closed_by: string | null;
  closed_at: string | null;
  conclusion: string | null;
}

export interface InvestigationItem {
  id: string;
  investigation_id: string;
  subject_type: InvestigationSubjectType;
  subject_id: string;
  added_by: string;
  added_at: string;
  note: string | null;
}

export interface UseCaseOutcomeCounts {
  use_case: string;
  status_counts: Record<string, number>;
}

export interface ApprovalRateEstimate {
  point_estimate: number;
  ci_low: number;
  ci_high: number;
  sample_size: number;
}

export interface EvaluationMetrics {
  total_runs: number;
  run_status_counts: Record<string, number>;
  use_case_breakdown: UseCaseOutcomeCounts[];
  total_interrupts: number;
  open_interrupts: number;
  resolved_interrupts: number;
  decision_counts: Record<string, number>;
  recent_reject_edit_reasons: HitlInterrupt[];
  approval_rate: ApprovalRateEstimate | null;
}

export interface IsolationForestValidation {
  roc_auc_mean: number;
  roc_auc_std: number;
  precision_at_p90_mean: number;
  recall_at_p90_mean: number;
  fold_count: number;
}

export interface HdbscanStabilityResult {
  noise_fraction_mean: number;
  noise_fraction_std: number;
  cluster_count_mean: number;
  resample_count: number;
}

export interface CombinerAblationEntry {
  signal_name: string;
  auc_without_signal: number;
  delta: number;
}

export interface ModelValidationResult {
  transaction_count: number;
  flagged_count: number;
  isolation_forest: IsolationForestValidation | null;
  hdbscan_stability: HdbscanStabilityResult | null;
  baseline_combined_auc: number | null;
  combiner_ablation: CombinerAblationEntry[];
}
