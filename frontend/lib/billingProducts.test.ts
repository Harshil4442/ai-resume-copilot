import { describe, expect, it } from "vitest";
import { finiteCreditBundles, isFiniteBundleSku } from "./billingProducts";

const bundle = {
  sku: "starter_bundle", name: "Server bundle", description: "Finite balances",
  amount_minor: 87654, amount_display: "₹876.54", currency: "INR", billing_type: "one_time",
  duration_days: 0, entitlement_kind: "credit_bundle", entitlement_quantity: 99,
  analysis_units: 2, job_service_credits: 99, auto_renews: false,
  catalog_visible: true, enabled_for_purchase: true,
};

describe("finite server-owned purchase offers", () => {
  it("retains exact server prices and quantities without a fallback catalog", () => {
    expect(finiteCreditBundles([bundle])).toEqual([bundle]);
    expect(finiteCreditBundles(undefined)).toEqual([]);
    expect(finiteCreditBundles([])).toEqual([]);
  });

  it.each(["premium_30d", "job_service_500", "unknown_bundle"])("rejects historical or unknown purchase SKU %s", (sku) => {
    expect(isFiniteBundleSku(sku)).toBe(false);
    expect(finiteCreditBundles([{ ...bundle, sku }])).toEqual([]);
  });

  it.each([
    { entitlement_kind: "premium_access" }, { entitlement_kind: "job_service_credits" },
    { amount_minor: 0 }, { amount_minor: 1.5 }, { amount_minor: Number.MAX_SAFE_INTEGER + 1 },
    { analysis_units: -1 }, { job_service_credits: Infinity }, { entitlement_quantity: 100 },
    { duration_days: 30 }, { auto_renews: true }, { currency: "USD" }, { enabled_for_purchase: "true" },
  ])("refuses malformed or nonfinite offer %j", (update) => {
    expect(finiteCreditBundles([{ ...bundle, ...update }])).toEqual([]);
  });

  it("refuses ambiguous duplicate SKUs and does not expose old offers mixed with valid bundles", () => {
    expect(finiteCreditBundles([bundle, { ...bundle, amount_minor: 12345 }])).toEqual([]);
    expect(finiteCreditBundles([{ ...bundle, sku: "premium_30d" }, bundle])).toEqual([bundle]);
  });
});
