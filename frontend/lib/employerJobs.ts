export const employerJobsBase = "/v1/employer-jobs";

export type JobServiceCatalog = {
  enabled: boolean;
  auto_submit_enabled: boolean;
  search_credits_per_job: number;
  apply_credits_per_job: number;
  max_search_jobs: number;
  balance: number;
  sources: { id: string; employer: string; platform: string; careers_url: string; last_success_at: string | null; status: string; application_mode: string }[];
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
};

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
