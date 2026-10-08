export const employerJobsBase = "/v1/employer-jobs";

export type AdmissionLimits = {
  version: string; daily_limit: number; rolling_limit: number; rolling_days: number;
  pending_limit: number; daily_credit_limit: number; rolling_credit_limit: number;
  batch_limit: number; batch_credit_limit: number; quote_hours: number;
  day_boundary: string; counting_policy: string;
};
export type EmployerAdmissionPolicy = {
  version: string; daily_limit: number; rolling_limit: number; rolling_days: number;
  evidence_url: string | null; evidence_note: string;
};
export type AdmissionQuote = {
  candidate: AdmissionLimits; employer: EmployerAdmissionPolicy;
  employer_key: string; opening_key: string; quoted_at: string; expires_at: string; policy_fingerprint: string;
};
export type ApplicationPrice = { unit_price: number; pricing_version: string };
export type ApplicationAction = "fill" | "upload" | "submit";

export type JobServiceCatalog = {
  enabled: boolean;
  auto_submit_enabled: boolean;
  search_credits_per_job: number;
  apply_credits_per_job: number;
  max_search_jobs: number;
  balance: number;
  admission_limits?: AdmissionLimits;
  sources: { id: string; employer: string; platform: string; careers_url: string; last_success_at: string | null; status: string; application_mode: string; employer_key?: string; admission_policy?: EmployerAdmissionPolicy }[];
};
export type EmployerPosting = {
  id: string;
  source_id: string;
  external_id: string;
  title: string;
  employer: string;
  location: string;
  description: string;
  canonical_url: string;
  apply_url: string;
  platform: string;
  publication_at: string | null;
  publication_kind?: string | null;
  last_checked_at: string;
  application_mode: string;
  opening_key?: string;
};
export type SearchResult = {
  posting: EmployerPosting;
  fit: { score: number; matched_skills: string[]; missing_evidence: string[]; reasons: string[]; scoring_version: string };
  charged_credits: number;
};
export type EmployerSearch = {
  id: string;
  status: string;
  desired_count: number;
  delivered_count: number;
  reserved_credits: number;
  charged_credits: number;
  refunded_credits: number;
  created_at: string;
  query: { resume_id: number; role: string; location: string; remote_only: boolean; published_within_days: number | null };
  items: SearchResult[];
};
export type ApplicationField = {
  id: string;
  label: string;
  type: "text" | "textarea" | "email" | "single_select" | "multi_select" | "file" | "hidden" | "consent";
  required: boolean;
  options: { value: string; label: string }[];
  group?: string;
};
export type ResumeChoice = "original" | "tailored" | "custom";
export type EmployerApplication = {
  id: string;
  status: "needs_action" | "ready" | "approved" | "queued" | "submitting" | "confirmed" | "failed" | "unknown" | "cancelled" | "manual_handoff";
  application_mode: string;
  posting: EmployerPosting;
  resume_id: number;
  resume_choice: ResumeChoice;
  resume_version_id: string | null;
  form: { version: string; fields: ApplicationField[]; consents: { id: string; label: string; required: boolean; policy_url?: string; text?: string }[] };
  package_digest: string;
  artifact: { sha256: string; filename: string; size_bytes: number; media_type: string; preview_url: string } | null;
  answers: Record<string, string | string[]>;
  consents: Record<string, boolean>;
  missing_fields: string[];
  credit_cost: number;
  charged_credits: number;
  reserved_credits?: number;
  cancel_requested?: boolean;
  receipt?: Record<string, unknown> | null;
  requires_user_action: boolean;
  error?: { code: string; message: string } | null;
  approval_expires_at?: string | null;
  opening_key?: string;
  employer_key?: string;
  admission_snapshot?: AdmissionQuote | null;
  pricing_snapshot?: ApplicationPrice | null;
  batch_id?: string | null;
  admission?: { id: string; state: string; admitted_at: string; possible_send_at: string | null; settled_at: string | null; release_reason: string | null } | null;
};

export type EmployerBatchItem = {
  application_id: string; package_digest: string; allowed_actions: ApplicationAction[];
  credit_cost: number; opening_key: string; employer_key: string;
  pricing_snapshot: ApplicationPrice; admission_snapshot: AdmissionQuote;
};
export type EmployerApplicationBatch = {
  id: string; status: "quoted" | "approved" | "queued" | "cancelled"; package_digest: string;
  items: EmployerBatchItem[]; admission_snapshot: AdmissionLimits;
  quoted_credits: number; max_total_credits: number; created_at: string; expires_at: string;
  approved_at: string | null; cancelled_at: string | null;
  application_statuses: Record<string, EmployerApplication["status"]>;
};

export function batchEligibility(application: EmployerApplication, catalog: JobServiceCatalog | undefined, now = Date.now()): string | null {
  if (!catalog?.enabled || !catalog.auto_submit_enabled || !catalog.admission_limits) return "Reviewed API batches are not enabled for this service.";
  if (application.application_mode !== "api" || application.posting.application_mode !== "api") return "Use this employer's individual manual handoff.";
  if (!catalog.sources.some((source) => source.id === application.posting.source_id && source.application_mode === "api")) return "This source does not currently offer a permissioned API route.";
  if (!["ready", "approved"].includes(application.status) || application.missing_fields.length) return "Complete and save this application's answers and consent choices first.";
  if (application.batch_id) return "This application already belongs to a reviewed batch.";
  if (!application.artifact || !["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].includes(application.artifact.media_type) || !safeEmployerUrl(application.posting.apply_url)) return "The exact retained PDF or DOCX and employer destination must be available.";
  if (!application.opening_key || !application.employer_key || !/^[a-f0-9]{64}$/.test(application.package_digest)) return "Refresh this application to obtain its current opening and package quote.";
  const quote = application.admission_snapshot;
  const price = application.pricing_snapshot;
  if (!quote || !price || price.unit_price !== application.credit_cost || !price.pricing_version || quote.opening_key !== application.opening_key || quote.employer_key !== application.employer_key) return "Refresh this application's fixed price and admission policy before batch review.";
  if (!Number.isFinite(Date.parse(quote.expires_at)) || Date.parse(quote.expires_at) <= now) return "This application's quote expired. Save and review it again.";
  return null;
}

export function batchItemMatches(item: EmployerBatchItem, application: EmployerApplication | undefined): boolean {
  return Boolean(application && application.id === item.application_id && application.package_digest === item.package_digest &&
    application.opening_key === item.opening_key && application.employer_key === item.employer_key &&
    application.credit_cost === item.credit_cost && application.pricing_snapshot?.unit_price === item.pricing_snapshot.unit_price &&
    application.pricing_snapshot?.pricing_version === item.pricing_snapshot.pricing_version &&
    application.admission_snapshot?.policy_fingerprint === item.admission_snapshot.policy_fingerprint &&
    item.allowed_actions.includes("upload") && item.allowed_actions.includes("submit"));
}

export function safeEmployerUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch { return null; }
}

export const applicationStatusLabels: Record<EmployerApplication["status"], string> = {
  needs_action: "Needs your answers", ready: "Ready for review", approved: "Approved", queued: "Queued",
  submitting: "Submission in progress", confirmed: "Submission confirmed", failed: "Could not complete",
  unknown: "Outcome unknown", cancelled: "Cancelled", manual_handoff: "Continue on employer portal",
};
