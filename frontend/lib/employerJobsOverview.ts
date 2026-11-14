import { apiGet } from "./api";
import { applicationStatusLabels, employerJobsBase, type EmployerApplicationListItem, type EmployerSearchSummaryPage, type JobServiceCatalog } from "./employerJobs";
import type { ResumePageResponse } from "./types";
import type { EmployerJobsRead } from "./useEmployerJobsReads";

export const employerJobsOverviewKey = ["employer-jobs", "bootstrap"] as const;

export type EmployerJobsOverview = {
  catalog: JobServiceCatalog;
  resumes: ResumePageResponse;
  saved_searches: EmployerSearchSummaryPage;
  applications: EmployerApplicationListItem[];
};

export type JobsFirstPage<T> = {
  data: T | undefined;
  isPending: boolean;
  isFetching: boolean;
  isSuccess: boolean;
  isError: boolean;
  error: Error | null;
  refetch: () => unknown;
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cursor(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function date(value: unknown): boolean {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function strings(...values: unknown[]): boolean {
  return values.every((value) => typeof value === "string");
}

function integers(...values: unknown[]): boolean {
  return values.every(Number.isSafeInteger);
}

function resume(value: unknown): boolean {
  return record(value) && Number.isSafeInteger(value.id) && typeof value.filename === "string" &&
    date(value.created_at) && typeof value.source_available === "boolean" &&
    (value.source_format === null || (typeof value.source_format === "string" && ["pdf", "docx", "tex", "texzip"].includes(value.source_format)));
}

function catalog(value: unknown): boolean {
  if (!record(value) || typeof value.enabled !== "boolean" || typeof value.auto_submit_enabled !== "boolean" ||
      typeof value.pricing_version !== "string" ||
      !integers(value.balance, value.search_credits_per_job, value.apply_credits_per_job, value.max_search_jobs) ||
      !Array.isArray(value.sources) || !value.sources.every((source) => record(source) &&
        strings(source.id, source.employer, source.status, source.application_mode) &&
        (source.last_success_at === null || date(source.last_success_at)))) return false;
  const limit = value.admission_limits;
  return limit === undefined || (record(limit) && integers(limit.batch_limit, limit.batch_credit_limit));
}

function savedSearch(value: unknown): boolean {
  return record(value) && strings(value.id, value.status) && date(value.created_at) && record(value.query) && record(value.scope) &&
    integers(value.desired_count, value.delivered_count, value.reserved_credits, value.charged_credits, value.refunded_credits);
}

function application(value: unknown): boolean {
  if (!record(value) || !strings(value.id, value.application_mode, value.package_digest) ||
      typeof value.status !== "string" || !Object.hasOwn(applicationStatusLabels, value.status) ||
      !Number.isSafeInteger(value.credit_cost) || typeof value.has_missing_fields !== "boolean" ||
      !record(value.posting) || !strings(value.posting.title, value.posting.employer, value.posting.source_id, value.posting.application_mode) ||
      typeof value.posting.has_valid_apply_url !== "boolean" ||
      (value.batch_id !== undefined && value.batch_id !== null && typeof value.batch_id !== "string")) return false;
  const artifact = value.artifact;
  const price = value.pricing_snapshot;
  const admission = value.admission_snapshot;
  return (artifact === null || (record(artifact) && strings(artifact.filename, artifact.media_type))) &&
    (price == null || (record(price) && strings(price.pricing_version) && integers(price.unit_price))) &&
    (admission == null || (record(admission) && strings(admission.opening_key, admission.employer_key, admission.policy_fingerprint) && date(admission.expires_at)));
}

export async function readEmployerJobsOverview(signal: AbortSignal, read: EmployerJobsRead = apiGet): Promise<EmployerJobsOverview> {
  const value = await read<unknown>(`${employerJobsBase}/bootstrap`, signal);
  if (!record(value) || value.schema_version !== "owned-jobs-bootstrap-v1" ||
      !catalog(value.catalog) ||
      !record(value.resumes) || !cursor(value.resumes.next_cursor) || !Array.isArray(value.resumes.resumes) ||
      !value.resumes.resumes.every(resume) || (value.resumes.selected_resume !== null && !resume(value.resumes.selected_resume)) ||
      !record(value.saved_searches) || !cursor(value.saved_searches.next_cursor) || !Array.isArray(value.saved_searches.items) ||
      !value.saved_searches.items.every(savedSearch) ||
      !Array.isArray(value.applications) || !value.applications.every(application)) {
    throw new Error("Could not verify the Jobs overview.");
  }
  return {
    catalog: value.catalog as JobServiceCatalog, resumes: value.resumes as ResumePageResponse,
    saved_searches: value.saved_searches as EmployerSearchSummaryPage, applications: value.applications as EmployerApplicationListItem[],
  };
}
