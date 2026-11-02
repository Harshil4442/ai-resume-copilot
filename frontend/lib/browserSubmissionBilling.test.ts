import { describe, expect, it } from "vitest";
import vector from "../../backend/tests/fixtures/browser_submission_v5.json";
import { applicationSubmissionBilling, isSubmissionBilling, submissionCanonicalPayload, submissionReviewBillingMatches, submissionUnits, SUBMISSION_BILLING_KEYS } from "./browserSubmissionBilling";

describe("explicit immutable unified submission billing", () => {
  it("keeps signed lexical numeric-key ordering without mutating the original JSON", () => {
    const value = { answers: { "2": "two", "10": "ten" }, value: "quote \" \\ \n" };
    expect(submissionCanonicalPayload(value)).toBe('{"answers":{"10":"ten","2":"two"},"value":"quote \\" \\\\ \\n"}');
    expect(Object.keys(value.answers)).toEqual(["2", "10"]);
    expect(() => submissionCanonicalPayload(JSON.parse('{"constructor":"unsafe"}'))).toThrow();
  });
  it("accepts the complete cross-language quote, retaining legacy units independently", () => {
    expect(isSubmissionBilling(vector.unified5.review_response.billing)).toBe(true);
    expect(submissionReviewBillingMatches(vector.unified5.review_response)).toBe(true);
    expect(submissionReviewBillingMatches(vector.legacy4.review_response)).toBe(true);
    expect(submissionUnits(5)).toBe("3 unified credits");
    expect(submissionUnits(4)).toBe("20 legacy service credits");
  });
  it.each(SUBMISSION_BILLING_KEYS)("does not infer absent %s", key => {
    const quote: Record<string, unknown> = structuredClone(vector.unified5.review_response.billing);
    delete quote[key]; expect(isSubmissionBilling(quote)).toBe(false);
    expect(submissionReviewBillingMatches({ ...vector.unified5.review_response, billing: quote })).toBe(false);
  });
  it.each([null, [], {}, { ...vector.unified5.review_response.billing, billing_currency: "INR" },
    { ...vector.unified5.review_response.billing, credits: "3" }, { ...vector.unified5.review_response.billing, credits: 20 },
    { ...vector.unified5.review_response.billing, quote_expires_at_ms: true },
    { ...vector.unified5.review_response.billing, quote_expires_at_ms: Number.MAX_SAFE_INTEGER + 1 },
    { ...vector.unified5.review_response.billing, extra: 3 }])("refuses ambiguous billing %j", quote => expect(isSubmissionBilling(quote)).toBe(false));
  it("requires matching explicit top, payload and pricing; a legacy fee never negotiates protocol five", () => {
    const original = vector.unified5.review_response;
    for (const changed of [{ ...original, review_protocol_version: 4 }, { ...original, fee_credits: 20 },
      { ...original, payload: { ...original.payload, billing: undefined } },
      { ...original, payload: { ...original.payload, pricing: { unit_price: 3 } } },
      { ...original, payload: { ...original.payload, billing: { ...original.billing, quote_id: `csq_${"6".repeat(32)}` } } },
      { ...original, expires_at_ms: original.billing.quote_expires_at_ms + 1 }]) expect(submissionReviewBillingMatches(changed)).toBe(false);
    expect(submissionReviewBillingMatches({ ...vector.legacy4.review_response, billing: original.billing })).toBe(false);
  });
  it("retains exact quoted billing on terminal redaction without creating an actionable draft", () => {
    for (const state of ["unknown", "filled", "cancelled", "expired"]) expect(submissionReviewBillingMatches({ ...vector.unified5.review_response, payload: {}, state })).toBe(true);
    expect(submissionReviewBillingMatches({ ...vector.unified5.review_response, payload: {} })).toBe(false);
  });
  it("distinguishes an unquoted preparation estimate from an actual quote and hold", () => {
    expect(applicationSubmissionBilling({ credit_cost: 3, charged_credits: 0, reserved_credits: 0 })).toBe("legacy");
    const unquoted = { credit_cost: 3, charged_credits: 0, reserved_credits: 0, billing_currency: "credits" };
    expect(applicationSubmissionBilling(unquoted)).toBe("unquoted");
    expect(applicationSubmissionBilling({ ...unquoted, reserved_credits: 3 })).toBe("invalid");
    expect(applicationSubmissionBilling({ ...unquoted, reserved_credits: 3, billing: vector.unified5.review_response.billing })).toBe("quoted");
    expect(applicationSubmissionBilling({ ...unquoted, billing: null })).toBe("invalid");
    expect(applicationSubmissionBilling({ ...unquoted, reserved_credits: undefined })).toBe("invalid");
    expect(applicationSubmissionBilling({ ...unquoted, reserved_credits: true })).toBe("invalid");
    expect(applicationSubmissionBilling({ ...unquoted, reserved_credits: 3, charged_credits: 3, billing: vector.unified5.review_response.billing })).toBe("invalid");
    expect(applicationSubmissionBilling({ billing: vector.unified5.review_response.billing })).toBe("invalid");
  });
});
