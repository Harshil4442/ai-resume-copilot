import { describe, expect, it } from "vitest";
import { isUnifiedDiscovery, parseDiscoveryBatch, parseDiscoveryQuote } from "./unifiedDiscovery";
const batch = { id: "search_one", status: "completed", billing_currency: "credits", desired_count: 5, delivered_count: 2,
  charged_credits: 1, reserved_credits: 0, refunded_credits: 0, partial_batch: true, maximum_batch_size: 5, scope: {}, items: [{}, {}] };
const quote = { schema_version: "unified-service-quote-v1", quote_id: "csq_search", operation: "job_discovery_batch", input_fingerprint: "d".repeat(64),
  route_id: "discovery-v1", route_version: "1", mode: "native", tariff_version: "v1", credits: 1, deliverable: "Up to 5 new employer jobs.", supported_limits: { maximum_results: 5 },
  created_at: "2026-10-28T10:00:00Z", expires_at: "2026-10-28T10:15:00Z", server_time: "2026-10-28T10:00:00Z" };
describe("bounded discovery money contract", () => {
  it("reads one charge for a partial batch and no charge for an empty batch", () => {
    expect(parseDiscoveryBatch(batch)?.charged_credits).toBe(1);
    expect(parseDiscoveryBatch({ ...batch, delivered_count: 0, charged_credits: 0, refunded_credits: 1, partial_batch: false, items: [] })?.refunded_credits).toBe(1);
  });
  it.each([{ status: "unknown" }, { delivered_count: 6 }, { charged_credits: 2 }, { reserved_credits: 1 }, { partial_batch: false }, { items: [] }, { refunded_credits: 1 }, { billing_currency: "legacy_service_credits" }])("rejects inconsistent financial status %j", (change) => expect(parseDiscoveryBatch({ ...batch, ...change })).toBeNull());
  it("requires the qualified native one-credit bounded route", () => {
    expect(parseDiscoveryQuote(quote, 5)?.credits).toBe(1);
    for (const change of [{ credits: 2 }, { mode: "enhanced" }, { operation: "market_report" }, { supported_limits: { maximum_results: 100 } }, { supported_limits: { maximum_results: 4 } }]) expect(parseDiscoveryQuote({ ...quote, ...change }, 5)).toBeNull();
  });
  it("identifies public unified history without confusing legacy per-job prices", () => {
    expect(isUnifiedDiscovery({ billing_currency: "credits", price_quote: { pricing_unit: "completed_batch" } })).toBe(true);
    expect(isUnifiedDiscovery({ price_quote: { unit_price: 2 } })).toBe(false);
  });
});
