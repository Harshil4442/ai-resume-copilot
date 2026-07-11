import "server-only";

export type PublicCatalogProduct = {
  sku: "premium_30d";
  name: string;
  description: string;
  amount_minor: number;
  amount_display: string;
  currency: "INR";
  billing_type: "one_time";
  duration_days: 30;
  auto_renews: false;
  catalog_visible: boolean;
  enabled_for_purchase: boolean;
};

export type PublicBillingCatalog = {
  catalog_version: string;
  market: "IN";
  checkout_enabled: boolean;
  provider: "razorpay" | null;
  products: PublicCatalogProduct[];
};

function backendApiBase(): string | null {
  const configured = process.env.BACKEND_URL?.trim();
  if (!configured) return null;

  const base = configured.replace(/\/+$/, "");
  return base.endsWith("/api") ? base : `${base}/api`;
}

function isCatalog(value: unknown): value is PublicBillingCatalog {
  if (!value || typeof value !== "object") return false;
  const catalog = value as Partial<PublicBillingCatalog>;
  return (
    typeof catalog.catalog_version === "string" &&
    catalog.market === "IN" &&
    typeof catalog.checkout_enabled === "boolean" &&
    Array.isArray(catalog.products)
  );
}

export async function getPublicBillingCatalog(): Promise<PublicBillingCatalog | null> {
  const apiBase = backendApiBase();
  if (!apiBase) return null;

  try {
    const response = await fetch(`${apiBase}/public/billing/catalog`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;

    const data: unknown = await response.json();
    return isCatalog(data) ? data : null;
  } catch {
    return null;
  }
}
