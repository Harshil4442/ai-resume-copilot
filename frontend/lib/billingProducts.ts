export const FINITE_BUNDLE_SKUS = ["starter_bundle", "growth_bundle", "scale_bundle"] as const;
export type FiniteBundleSku = (typeof FINITE_BUNDLE_SKUS)[number];

export type CreditBundleProduct = {
  sku: FiniteBundleSku;
  name: string;
  description: string;
  amount_minor: number;
  amount_display: string;
  currency: "INR";
  billing_type: "one_time";
  duration_days: 0;
  entitlement_kind: "credit_bundle";
  entitlement_quantity: number;
  analysis_units: number;
  job_service_credits: number;
  auto_renews: false;
  catalog_visible: boolean;
  enabled_for_purchase: boolean;
};

export function isFiniteBundleSku(value: unknown): value is FiniteBundleSku {
  return typeof value === "string" && FINITE_BUNDLE_SKUS.includes(value as FiniteBundleSku);
}

function isCreditBundle(value: unknown): value is CreditBundleProduct {
  if (!value || typeof value !== "object") return false;
  const product = value as Partial<CreditBundleProduct>;
  return isFiniteBundleSku(product.sku)
    && product.entitlement_kind === "credit_bundle"
    && product.currency === "INR"
    && product.billing_type === "one_time"
    && product.duration_days === 0
    && product.auto_renews === false
    && typeof product.name === "string" && product.name.trim().length > 0
    && typeof product.description === "string"
    && typeof product.amount_display === "string"
    && Number.isSafeInteger(product.amount_minor) && (product.amount_minor ?? 0) > 0
    && Number.isSafeInteger(product.analysis_units) && (product.analysis_units ?? -1) >= 0
    && Number.isSafeInteger(product.job_service_credits) && (product.job_service_credits ?? 0) > 0
    && product.entitlement_quantity === product.job_service_credits
    && typeof product.catalog_visible === "boolean"
    && typeof product.enabled_for_purchase === "boolean";
}

// The server owns prices and quantities. Historical or malformed offers never
// become new purchases, and duplicate SKUs cannot create an ambiguous quote.
export function finiteCreditBundles(products: unknown): CreditBundleProduct[] {
  if (!Array.isArray(products)) return [];
  const bundles = products.filter(isCreditBundle);
  return new Set(bundles.map((product) => product.sku)).size === bundles.length ? bundles : [];
}
