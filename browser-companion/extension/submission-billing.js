// Protocol five is a distinct immutable unified-credit quote, never a fee alias.
export const SUBMISSION_BILLING_KEYS = Object.freeze(["billing_currency", "credits", "delivery_contract_sha256", "financial_policy_sha256", "input_fingerprint", "operation", "quote_contract_sha256", "quote_expires_at_ms", "quote_id", "route_id", "route_version", "schema_version", "tariff_version"]);
const HEX = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_.:-]{1,120}$/;
export function isSubmissionBilling(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join(",") === SUBMISSION_BILLING_KEYS.join(",")
    && value.schema_version === "unified-browser-submission-v1" && value.billing_currency === "credits"
    && value.operation === "verified_submission" && value.credits === 3
    && value.tariff_version === "credit-services-2026-10-27-v1"
    && typeof value.quote_id === "string" && /^csq_[a-f0-9]{32}$/.test(value.quote_id)
    && [value.route_id, value.route_version].every(part => typeof part === "string" && ID.test(part))
    && [value.delivery_contract_sha256, value.financial_policy_sha256, value.input_fingerprint, value.quote_contract_sha256].every(part => typeof part === "string" && HEX.test(part))
    && Number.isSafeInteger(value.quote_expires_at_ms) && value.quote_expires_at_ms > 0;
}
export const isSubmissionVersion = value => value === 4 || value === 5;
export function submissionUnits(version) {
  if (version === 5) return "3 unified credits";
  if (version === 4) return "20 legacy service credits";
  throw new Error("The submission credit unit is unavailable.");
}
