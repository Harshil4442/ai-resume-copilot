import { describe, expect, it } from "vitest";
import { isCreditOperationAvailable, parseCreditAvailability, parseUnifiedServiceQuote } from "./unifiedServiceQuotes";
const quote = { schema_version: "unified-service-quote-v1", quote_id: "csq_fixture", operation: "interview_questions", input_fingerprint: "a".repeat(64),
  route_id: "qualified-interview", route_version: "1", mode: "enhanced", tariff_version: "credit-services-v1", credits: 5,
  deliverable: "Eight distinct interview questions with preparation guidance.", supported_limits: { question_count: 8, max_file_bytes: 10000 },
  created_at: "2026-10-27T10:00:00Z", expires_at: "2026-10-27T10:15:00Z", server_time: "2026-10-27T10:00:00Z" };
describe("reviewed service quote contract", () => {
  it("preserves the qualified server price, promised delivery and supported limits", () => {
    expect(parseUnifiedServiceQuote(quote)).toEqual(quote);
    expect(parseUnifiedServiceQuote({ ...quote, credits: 6 })?.credits).toBe(6);
    expect(parseUnifiedServiceQuote({ ...quote, mode: "native", operation: "market_report" })?.mode).toBe("native");
  });
  it.each([{ schema_version: "legacy" }, { operation: "unknown" }, { input_fingerprint: "abc" }, { input_fingerprint: "A".repeat(64) },
    { route_id: "" }, { mode: "basic" }, { credits: 0 }, { credits: 1.5 }, { credits: "5" }, { deliverable: "" },
    { supported_limits: { max_questions: -1 } }, { supported_limits: { max_questions: "8" } }, { supported_limits: [] },
    { supported_limits: Object.fromEntries(Array.from({ length: 13 }, (_, i) => [`limit_${i}`, 1])) },
    { expires_at: "2026-10-27T10:15:01Z" }, { expires_at: quote.created_at }, { created_at: "2026-10-27T10:01:00Z" },
    { server_time: "2026-10-27T10:00:00" }, { expires_at: "2026-02-30T10:00:00Z" },
  ])("rejects malformed or unbounded approval terms %j", (changes) => { expect(parseUnifiedServiceQuote({ ...quote, ...changes })).toBeNull(); });
  it("allows an expired quote to be displayed as expired without reviving it", () => {
    expect(parseUnifiedServiceQuote({ ...quote, server_time: "2026-10-27T11:00:00Z" })?.expires_at).toBe(quote.expires_at);
  });
  it("requires explicit availability and qualified operation names", () => {
    const closed = { catalog_enabled: true, execution_enabled: false, supported_operations: [] };
    expect(parseCreditAvailability(closed)).toEqual({ ...closed, free_core_operations: [], free_core_limits: {} });
    for (const value of [{}, { catalog_enabled: true }, { ...closed, supported_operations: undefined },
      { ...closed, catalog_enabled: "true" }, { ...closed, execution_enabled: true },
      { ...closed, supported_operations: ["interview_questions"] },
      { ...closed, execution_enabled: true, supported_operations: ["unknown"] },
      { ...closed, execution_enabled: true, supported_operations: ["interview_questions", "interview_questions"] }, null]) {
      expect(parseCreditAvailability(value)).toBeNull();
    }
  });
  it("enables only the specifically qualified execution route", () => {
    const availability = parseCreditAvailability({ catalog_enabled: false, execution_enabled: true, supported_operations: ["interview_questions"] });
    expect(isCreditOperationAvailable(availability, "interview_questions")).toBe(true);
    expect(isCreditOperationAvailable(availability, "resume_tailor")).toBe(false);
    expect(isCreditOperationAvailable(undefined, "interview_questions")).toBe(false);
  });
});


it("requires a complete funded allowance before describing an operation as free", () => {
  const closed = { catalog_enabled: false, execution_enabled: false, supported_operations: [] };
  const limits = { per_account_30_days: 10, max_input_bytes: 1024, max_source_records: 20 };
  const funded = { ...closed, free_core_operations: ["basic_match"], free_core_limits: { basic_match: limits } };
  expect(parseCreditAvailability(funded)?.free_core_limits.basic_match).toEqual(limits);
  for (const change of [{ free_core_limits: {} }, { free_core_operations: [] },
    { free_core_limits: { basic_match: { ...limits, per_account_30_days: 0 } } }, { free_core_operations: ["unsupported"] }]) {
    expect(parseCreditAvailability({ ...funded, ...change })).toBeNull();
  }
});
