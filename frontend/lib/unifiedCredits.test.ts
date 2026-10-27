const eligibleServices = [{ operation: "resume_tailor", credits: 8, deliverable: "Tailored resume with preserved source layout.", supported_limits: { max_pages: 2 }, source_formats: ["pdf", "docx"], route_version: "reviewed-v1" }];
import { describe, expect, it } from "vitest";
import {
  campaignStatusAt, creditTimestamp, formatCreditPrice, packNeedsPriceRefresh,
  parseUnifiedCreditCatalog, parseUnifiedPurchaseQuote, purchaseQuoteExpired, showReferencePrice,
} from "./unifiedCredits";

const campaign = {
  id: "launch-2026", version: "1", starts_at: "2026-10-01T00:00:00Z",
  ends_at: "2026-10-31T00:00:00Z", timezone: "Asia/Kolkata", status: "active",
  standard_amount_minor: 7900, reference_price_verified: false,
};
const pack = { sku: "starter_pass", name: "Starter", amount_minor: 5900, credits: 40,
  duration_days: 30, auto_renews: false, purchase_enabled: true,
  terms_version: "credit-pass-2026-10-27-v1", campaign };
const catalog = { schema_version: "unified-credits-v1", eligible_services: eligibleServices, catalog_version: "inr-unified-2026-10-27-v1",
  server_time: "2026-10-30T23:59:00Z", market: "IN", currency: "INR", checkout_enabled: true,
  products: [pack] };
const quote = { schema_version: "unified-credits-v1", quote_id: "quote-owner-bound",
  sku: "starter_pass", name: "Starter", eligible_services: eligibleServices, catalog_version: catalog.catalog_version,
  terms_version: pack.terms_version, amount_minor: 5900, currency: "INR", credits: 40,
  duration_days: 30, auto_renews: false, created_at: "2026-10-30T23:59:00Z",
  expires_at: "2026-10-31T00:14:00Z", pass_expires_at: "2026-11-29T23:59:00Z",
  server_time: "2026-10-30T23:59:00Z", campaign };

describe("unified credit catalog", () => {
  it("retains server-owned prices and one finite allowance without a fallback catalog", () => {
    expect(parseUnifiedCreditCatalog(catalog)).toEqual(catalog);
    const updated = { ...pack, amount_minor: 6100, credits: 43 };
    expect(parseUnifiedCreditCatalog({ ...catalog, products: [updated] })?.products[0]).toMatchObject(updated);
    expect(parseUnifiedCreditCatalog(undefined)).toBeNull();
    expect(parseUnifiedCreditCatalog({ ...catalog, products: [] })?.products).toEqual([]);
  });

  it.each([
    { schema_version: "dual-balances-v1" }, { currency: "USD" }, { market: "US" },
    { checkout_enabled: "true" }, { server_time: "2026-10-30T23:59:00" },
    { eligible_services: eligibleServices, catalog_version: "" }, { products: [pack, pack] },
  ])("refuses unsupported or ambiguous catalog %j", (change) => {
    expect(parseUnifiedCreditCatalog({ ...catalog, ...change })).toBeNull();
  });

  it.each([
    { sku: "starter_bundle" }, { credits: 0 }, { credits: 1.1 }, { amount_minor: Infinity },
    { amount_minor: Number.MAX_SAFE_INTEGER + 1 }, { duration_days: 0 },
    { auto_renews: true }, { purchase_enabled: "true" }, { terms_version: "" },
    { campaign: undefined }, { campaign: { ...campaign, timezone: "Unknown/Region" } },
    { campaign: { ...campaign, starts_at: campaign.ends_at } },
    { campaign: { ...campaign, status: "scheduled" } },
  ])("refuses an invalid pass instead of silently changing its terms %j", (change) => {
    expect(parseUnifiedCreditCatalog({ ...catalog, products: [{ ...pack, ...change }] })).toBeNull();
  });

  it("accepts standard-price passes with no campaign", () => {
    expect(parseUnifiedCreditCatalog({ ...catalog, products: [{ ...pack, amount_minor: 7900, campaign: null }] })?.products[0].campaign).toBeNull();
  });

  it("requires positive verified evidence to show a crossed-out reference", () => {
    const missing = { ...campaign, reference_price_verified: undefined };
    const parsed = parseUnifiedCreditCatalog({ ...catalog, products: [{ ...pack, campaign: missing }] })!;
    expect(parsed.products[0].campaign?.reference_price_verified).toBe(false);
    expect(showReferencePrice(parsed.products[0], Date.parse(catalog.server_time))).toBe(false);
    const verified = parseUnifiedCreditCatalog({ ...catalog, products: [{ ...pack, campaign: { ...campaign, reference_price_verified: true } }] })!;
    expect(showReferencePrice(verified.products[0], Date.parse(catalog.server_time))).toBe(true);
    expect(showReferencePrice(verified.products[0], Date.parse(campaign.ends_at))).toBe(false);
    expect(parseUnifiedCreditCatalog({ ...catalog, products: [{ ...pack, campaign: { ...campaign, standard_amount_minor: 5900, reference_price_verified: true } }] })).toBeNull();
  });

  it("marks a catalog stale at real campaign boundaries without synthesizing a new price", () => {
    const parsed = parseUnifiedCreditCatalog(catalog)!.products[0];
    const end = Date.parse(campaign.ends_at);
    expect(packNeedsPriceRefresh(parsed, end - 1)).toBe(false);
    expect(packNeedsPriceRefresh(parsed, end)).toBe(true);
    expect(parsed.amount_minor).toBe(5900);
    expect(campaignStatusAt(parsed.campaign!, Date.parse(campaign.starts_at) - 1)).toBe("scheduled");
    expect(campaignStatusAt(parsed.campaign!, Date.parse(campaign.starts_at))).toBe("active");
  });
});

describe("immutable 15-minute purchase quote", () => {
  it("honors the original price across campaign expiry", () => {
    const replay = parseUnifiedPurchaseQuote({ ...quote, server_time: "2026-10-31T00:05:00Z" });
    expect(replay).toMatchObject({ amount_minor: 5900, credits: 40, quote_id: quote.quote_id });
    expect(purchaseQuoteExpired(replay!, Date.parse(replay!.server_time))).toBe(false);
    expect(purchaseQuoteExpired(replay!, Date.parse(quote.expires_at))).toBe(true);
  });

  it("parses an expired historical quote so the UI can require a fresh review", () => {
    const expired = parseUnifiedPurchaseQuote({ ...quote, server_time: "2026-10-31T01:00:00Z" });
    expect(expired).not.toBeNull();
    expect(purchaseQuoteExpired(expired!, Date.parse(expired!.server_time))).toBe(true);
  });

  it.each([
    { quote_id: "" }, { currency: "USD" }, { credits: -2 }, { auto_renews: true },
    { expires_at: "2026-10-31T00:14:01Z" }, { expires_at: quote.created_at },
    { pass_expires_at: "2026-11-29T23:58:59Z" }, { server_time: "2026-10-30T23:58:00Z" },
    { server_time: undefined }, { campaign: { ...campaign, status: "ended" } },
  ])("fails closed on malformed or shortened terms %j", (change) => {
    expect(parseUnifiedPurchaseQuote({ ...quote, ...change })).toBeNull();
  });

  it("formats integer minor amounts without trusting an amount_display string", () => {
    expect(formatCreditPrice(5900)).toBe("₹59");
    expect(formatCreditPrice(17900)).toBe("₹179");
    expect(formatCreditPrice(44900)).toBe("₹449");
    expect(formatCreditPrice(12345)).toBe("₹123.45");
  });

  it.each(["not a date", "2026-10-27", "2026-10-27T10:00:00", "2026-02-29T00:00:00Z", "2026-02-30T00:00:00Z", "2026-10-27T24:00:00Z", "2026-10-27T23:59:00+25:00", 123, null])("rejects ambiguous or normalized-invalid timestamp %j", (value) => {
    expect(creditTimestamp(value)).toBeNull();
  });

  it("accepts real leap days, explicit offsets and provider microseconds", () => {
    expect(creditTimestamp("2028-02-29T23:59:00Z")).toBe(Date.parse("2028-02-29T23:59:00Z"));
    expect(creditTimestamp("2026-10-27T12:30:00.123456+05:30")).toBe(Date.parse("2026-10-27T07:00:00.123Z"));
  });
});

import { parseUnifiedCreditWallet, parseUnifiedCreditHistory, parseUnifiedCreditOrder, parseUnifiedCheckoutOrder, sameUnifiedPurchaseQuote } from "./unifiedCredits";
const wallet = { schema_version: "unified-credits-v1", server_time: quote.server_time, available_credits: 7, reserved_credits: 2, debt_credits: 0,
  lots: [{ grant_id: "grant-1", remaining_credits: 9, expires_at: quote.pass_expires_at, funding_kind: "paid" }] };
const order = { schema_version: "unified-credits-v1", order_id: "order-1", provider_order_id: "provider-1", status: "pending", payment_status: "created", fulfilled: false,
  checkout_available: true, amount_minor: quote.amount_minor, refunded_amount_minor: 0, currency: "INR", sku: quote.sku, created_at: quote.created_at, paid_at: null, pass_expires_at: null, accepted_quote: quote };
const checkout = { schema_version: "unified-credits-v1", order_id: "order-1", provider_order_id: "provider-1", provider: "razorpay", key_id: "public-fixture", amount_minor: quote.amount_minor,
  currency: "INR", name: "HireWiz", description: "40 credits", accepted_quote: quote, prefill: { email: "owner@example.test" } };
describe("unified wallet, history and original payment contracts", () => {
  it("preserves the authoritative spendable balance without summing grants or older currencies", () => {
    expect(parseUnifiedCreditWallet(wallet)).toEqual(wallet);
    expect(parseUnifiedCreditWallet({ ...wallet, lots: [{ ...wallet.lots[0], expires_at: null, funding_kind: "legacy" }] })?.lots[0].expires_at).toBeNull();
    expect(parseUnifiedCreditWallet({ ...wallet, lots: [...wallet.lots, ...wallet.lots] })).toBeNull();
    expect(parseUnifiedCreditWallet({ ...wallet, reserved_credits: -1 })).toBeNull();
  });
  it("drops private event details from the display contract and validates a page cursor", () => {
    const event = { id: "event-1", event_type: "grant", amount: 40, balance_after: 40, created_at: quote.created_at };
    expect(parseUnifiedCreditHistory({ schema_version: "unified-credits-v1", items: [{ ...event, details: { internal_id: "not-for-ui" } }], next_cursor: "next-safe" }))
      .toEqual({ schema_version: "unified-credits-v1", items: [event], next_cursor: "next-safe" });
    expect(parseUnifiedCreditHistory({ schema_version: "unified-credits-v1", items: [event, event] })).toBeNull();
    expect(parseUnifiedCreditHistory({ schema_version: "unified-credits-v1", items: [event], next_cursor: {} })).toBeNull();
  });
  it("accepts original order terms even after their quote expired", () => {
    const old = { ...quote, server_time: "2026-11-01T00:00:00Z" };
    const parsed = parseUnifiedCreditOrder({ ...order, accepted_quote: old });
    expect(parsed?.accepted_quote.quote_id).toBe(quote.quote_id);
    expect(sameUnifiedPurchaseQuote(parsed!.accepted_quote, parseUnifiedPurchaseQuote(quote)!)).toBe(true);
  });
  it.each([{ amount_minor: 6000 }, { refunded_amount_minor: 6000 }, { fulfilled: true }, { provider_order_id: null }, { currency: "USD" }, { pass_expires_at: "2026-10-31T00:00:00Z" }])("rejects inconsistent order terms %j", (change) => {
    expect(parseUnifiedCreditOrder({ ...order, ...change })).toBeNull();
  });
  it("allows an unknown creation only as an unpayable status", () => {
    expect(parseUnifiedCreditOrder({ ...order, provider_order_id: null, checkout_available: false, payment_status: "creation_unknown" })?.checkout_available).toBe(false);
  });
  it.each([{ accepted_quote: { ...quote, credits: 41 } }, { accepted_quote: { ...quote, terms_version: "changed" } }, { accepted_quote: { ...quote, campaign: { ...campaign, id: "another" } } }, { amount_minor: 6000 }, { currency: "USD" }, { provider: "other" }])("does not open a processor for a different accepted purchase %j", (change) => {
    expect(parseUnifiedCheckoutOrder({ ...checkout, ...change }, parseUnifiedPurchaseQuote(quote)!)).toBeNull();
  });
  it("accepts only a matching checkout snapshot while allowing refreshed server time", () => {
    const later = { ...quote, server_time: "2026-10-31T00:05:00Z" };
    expect(parseUnifiedCheckoutOrder({ ...checkout, accepted_quote: later }, parseUnifiedPurchaseQuote(quote)!)).not.toBeNull();
  });
});


it("retains exact eligible deliverables in immutable quote matching", () => {
  const accepted = parseUnifiedPurchaseQuote(quote)!;
  const changed = parseUnifiedPurchaseQuote({ ...quote, eligible_services: [{ ...eligibleServices[0], credits: 9 }] })!;
  expect(sameUnifiedPurchaseQuote(accepted, changed)).toBe(false);
  expect(parseUnifiedCheckoutOrder({ ...checkout, accepted_quote: changed }, accepted)).toBeNull();
  expect(parseUnifiedCreditCatalog({ ...catalog, eligible_services: [] })).toBeNull();
  expect(parseUnifiedCreditCatalog({ ...catalog, eligible_services: undefined })).toBeNull();
  const historic = parseUnifiedPurchaseQuote({ ...quote, eligible_services: undefined })!;
  expect(historic.eligible_services).toEqual([]);
  expect(parseUnifiedCheckoutOrder({ ...checkout, accepted_quote: historic }, historic)).toBeNull();
});
it.each<Record<string, unknown>>([{ credits: -1 }, { supported_limits: { max_pages: -1 } }, { supported_limits: { constructor: 1 } }, { source_formats: ["pdf", "pdf"] }, { deliverable: "" }])("refuses malformed eligible-service terms %j", (change) => {
  expect(parseUnifiedPurchaseQuote({ ...quote, eligible_services: [{ ...eligibleServices[0], ...change }] })).toBeNull();
});
