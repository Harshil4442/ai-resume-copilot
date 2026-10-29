import { creditTimestamp } from "./unifiedCredits";

export const SERVICE_QUOTE_SCHEMA = "unified-service-quote-v1" as const;
export const CREDIT_OPERATIONS = ["resume_enrichment", "enhanced_job_match", "interview_questions", "resume_tailor",
  "resume_tailor_extended", "bullet_rewrite", "enhanced_qa", "learning_strategy", "job_discovery_batch", "verified_submission", "market_report"] as const;
export type CreditOperation = typeof CREDIT_OPERATIONS[number];
export type UnifiedServiceQuote = {
  schema_version: typeof SERVICE_QUOTE_SCHEMA;
  quote_id: string;
  operation: CreditOperation;
  input_fingerprint: string;
  route_id: string;
  route_version: string;
  mode: "enhanced" | "native";
  tariff_version: string;
  credits: number;
  deliverable: string;
  supported_limits: Record<string, number>;
  created_at: string;
  expires_at: string;
  server_time: string;
};
export const FREE_CORE_OPERATIONS = ["resume_parse", "basic_match", "curated_interview", "skill_roi", "curated_learning"] as const;
export type FreeCoreOperation = typeof FREE_CORE_OPERATIONS[number];
export type FreeCoreLimits = { per_account_30_days: number; max_input_bytes: number; max_source_records: number };
export type CreditAvailability = { catalog_enabled: boolean; execution_enabled: boolean; supported_operations: CreditOperation[];
  free_core_operations: FreeCoreOperation[]; free_core_limits: Partial<Record<FreeCoreOperation, FreeCoreLimits>> };

function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function identifier(value: unknown): value is string { return typeof value === "string" && /^[A-Za-z0-9_.:-]{1,120}$/.test(value); }
export function isCreditOperation(value: unknown): value is CreditOperation { return typeof value === "string" && CREDIT_OPERATIONS.includes(value as CreditOperation); }
export function parseCreditAvailability(value: unknown): CreditAvailability | null {
  if (!object(value) || typeof value.catalog_enabled !== "boolean" || typeof value.execution_enabled !== "boolean"
    || !Array.isArray(value.supported_operations) || !value.supported_operations.every(isCreditOperation)
    || new Set(value.supported_operations).size !== value.supported_operations.length
    || value.execution_enabled !== (value.supported_operations.length > 0)) return null;
  const freeOperations = value.free_core_operations === undefined ? [] : value.free_core_operations;
  const freeLimits = value.free_core_limits === undefined ? {} : value.free_core_limits;
  if (!Array.isArray(freeOperations) || !freeOperations.every((operation) => FREE_CORE_OPERATIONS.includes(operation))
    || new Set(freeOperations).size !== freeOperations.length || !object(freeLimits)
    || Object.keys(freeLimits).length !== freeOperations.length) return null;
  const limits: CreditAvailability["free_core_limits"] = {};
  for (const operation of freeOperations as FreeCoreOperation[]) {
    const envelope = freeLimits[operation];
    if (!object(envelope) || ![envelope.per_account_30_days, envelope.max_input_bytes, envelope.max_source_records]
      .every((number) => typeof number === "number" && Number.isSafeInteger(number) && number > 0)) return null;
    limits[operation] = { per_account_30_days: envelope.per_account_30_days as number,
      max_input_bytes: envelope.max_input_bytes as number, max_source_records: envelope.max_source_records as number };
  }
  return { catalog_enabled: value.catalog_enabled, execution_enabled: value.execution_enabled, supported_operations: [...value.supported_operations],
    free_core_operations: [...freeOperations], free_core_limits: limits };
}
export function isCreditOperationAvailable(availability: CreditAvailability | null | undefined, operation: CreditOperation) {
  return availability?.execution_enabled === true && availability.supported_operations.includes(operation);
}
export function parseUnifiedServiceQuote(value: unknown): UnifiedServiceQuote | null {
  if (!object(value) || value.schema_version !== SERVICE_QUOTE_SCHEMA || !identifier(value.quote_id) || !isCreditOperation(value.operation)
    || typeof value.input_fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(value.input_fingerprint)
    || !identifier(value.route_id) || !identifier(value.route_version) || !identifier(value.tariff_version)
    || !["enhanced", "native"].includes(String(value.mode))
    || typeof value.credits !== "number" || !Number.isSafeInteger(value.credits) || value.credits <= 0
    || typeof value.deliverable !== "string" || !value.deliverable.trim() || value.deliverable.length > 500
    || !object(value.supported_limits) || Object.keys(value.supported_limits).length > 12) return null;
  const limits = Object.entries(value.supported_limits);
  if (limits.some(([key, number]) => !/^[a-z][a-z0-9_]{0,79}$/.test(key) || ["constructor", "prototype"].includes(key)
    || typeof number !== "number" || !Number.isSafeInteger(number) || number < 0)) return null;
  const created = creditTimestamp(value.created_at), expires = creditTimestamp(value.expires_at), now = creditTimestamp(value.server_time);
  if (created === null || expires === null || now === null || created > now || expires <= created || expires - created > 15 * 60_000) return null;
  return { schema_version: SERVICE_QUOTE_SCHEMA, quote_id: value.quote_id, operation: value.operation,
    input_fingerprint: value.input_fingerprint, route_id: value.route_id, route_version: value.route_version,
    mode: value.mode as UnifiedServiceQuote["mode"], tariff_version: value.tariff_version, credits: value.credits,
    deliverable: value.deliverable, supported_limits: Object.fromEntries(limits) as Record<string, number>,
    created_at: value.created_at as string, expires_at: value.expires_at as string, server_time: value.server_time as string };
}

const OPERATION_LABELS: Record<CreditOperation, string> = {
  resume_enrichment: "Resume skill enrichment", enhanced_job_match: "Enhanced job match", interview_questions: "Interview preparation",
  resume_tailor: "Resume tailoring", resume_tailor_extended: "Extended resume tailoring", bullet_rewrite: "Resume bullet rewrite",
  enhanced_qa: "Grounded question and answer", learning_strategy: "Learning strategy", job_discovery_batch: "Employer job discovery",
  verified_submission: "Verified employer submission", market_report: "Market report",
};
export function creditOperationLabel(operation: CreditOperation) { return OPERATION_LABELS[operation]; }
const LIMIT_LABELS: Record<string, string> = {
  max_file_bytes: "Maximum file size in bytes", max_pages: "Maximum pages", max_edits: "Maximum edits", max_bullets: "Maximum bullets",
  question_count: "Questions included", max_questions: "Maximum questions", max_jobs: "Maximum job results",
  max_input_chars: "Maximum input characters", max_output_words: "Maximum answer words", max_answer_chars: "Maximum answer characters",
};
export function serviceLimitLabel(key: string) { return LIMIT_LABELS[key] ?? key.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()); }
