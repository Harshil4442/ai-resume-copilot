// Explicit wire fields: a legacy fee or missing field never selects unified money.
export type SubmissionBilling = {
  schema_version: "unified-browser-submission-v1"; billing_currency: "credits";
  operation: "verified_submission"; credits: 3; tariff_version: "credit-services-2026-10-27-v1";
  quote_id: string; input_fingerprint: string; quote_contract_sha256: string; quote_expires_at_ms: number;
  route_id: string; route_version: string; delivery_contract_sha256: string; financial_policy_sha256: string;
};
export const SUBMISSION_BILLING_KEYS = ["billing_currency", "credits", "delivery_contract_sha256", "financial_policy_sha256", "input_fingerprint", "operation", "quote_contract_sha256", "quote_expires_at_ms", "quote_id", "route_id", "route_version", "schema_version", "tariff_version"] as const;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const hex = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const identifier = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9_.:-]{1,120}$/.test(value);
export function isSubmissionBilling(value: unknown): value is SubmissionBilling {
  return object(value) && Object.keys(value).sort().join(",") === SUBMISSION_BILLING_KEYS.join(",")
    && value.schema_version === "unified-browser-submission-v1" && value.billing_currency === "credits"
    && value.operation === "verified_submission" && value.credits === 3
    && value.tariff_version === "credit-services-2026-10-27-v1"
    && typeof value.quote_id === "string" && /^csq_[a-f0-9]{32}$/.test(value.quote_id)
    && identifier(value.route_id) && identifier(value.route_version)
    && [value.delivery_contract_sha256, value.financial_policy_sha256, value.input_fingerprint, value.quote_contract_sha256].every(hex)
    && typeof value.quote_expires_at_ms === "number" && Number.isSafeInteger(value.quote_expires_at_ms) && value.quote_expires_at_ms > 0;
}
// Match the original signed companion serialization, including numeric object
// keys. JSON.stringify's replacer would reorder those keys numerically.
export function submissionCanonicalPayload(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(submissionCanonicalPayload).join(",")}]`;
  if (!object(value)) throw new Error("Unsupported priced review value.");
  return `{${Object.keys(value).sort().map(key => {
    if (["__proto__", "prototype", "constructor"].includes(key)) throw new Error("Unsafe priced review field.");
    return `${JSON.stringify(key)}:${submissionCanonicalPayload(value[key])}`;
  }).join(",")}}`;
}
export function submissionUnits(version: 4 | 5): string {
  if (version === 5) return "3 unified credits";
  if (version === 4) return "20 legacy service credits";
  throw new Error("The submission credit unit is unavailable.");
}
export function submissionReviewBillingMatches(value: unknown): boolean {
  if (!object(value)) return false;
  const payload = object(value.payload) && Object.keys(value.payload).length ? value.payload : null;
  const version = value.review_protocol_version ?? payload?.review_protocol_version;
  if (version !== 5) return value.billing === undefined && payload?.billing === undefined;
  if (value.review_protocol_version !== 5 || !isSubmissionBilling(value.billing) || value.fee_credits !== 3
    || typeof value.expires_at_ms !== "number" || !Number.isSafeInteger(value.expires_at_ms)
    || value.expires_at_ms <= 0 || value.expires_at_ms > value.billing.quote_expires_at_ms) return false;
  if (!payload) return ["filled", "unknown", "cancelled", "expired"].includes(value.state as string);
  if (payload.review_protocol_version !== 5 || payload.fee_credits !== 3 || !isSubmissionBilling(payload.billing)
    || !isSubmissionBilling(payload.pricing)) return false;
  return SUBMISSION_BILLING_KEYS.every(key => value.billing && (value.billing as SubmissionBilling)[key] === (payload.billing as SubmissionBilling)[key]
    && (value.billing as SubmissionBilling)[key] === (payload.pricing as SubmissionBilling)[key]);
}

// Preparation can name the unified wallet before a priced review exists. The
// three-credit advisory cost is never a quote, reservation, or conversion.
export function applicationSubmissionBilling(value: unknown): "legacy" | "unquoted" | "quoted" | "invalid" {
  if (!object(value)) return "invalid";
  if (value.billing_currency === undefined && value.billing === undefined) return "legacy";
  if (value.billing_currency !== "credits") return "invalid";
  if (typeof value.charged_credits !== "number" || typeof value.reserved_credits !== "number"
    || ![0, 3].includes(value.charged_credits) || ![0, 3].includes(value.reserved_credits)
    || value.charged_credits + value.reserved_credits > 3) return "invalid";
  if (value.billing === undefined) return value.charged_credits === 0 && value.reserved_credits === 0 ? "unquoted" : "invalid";
  return value.credit_cost === 3 && isSubmissionBilling(value.billing) ? "quoted" : "invalid";
}
