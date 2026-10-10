import "server-only";

export type PublicCatalogProduct = {
  sku: "starter_bundle" | "growth_bundle" | "scale_bundle";
  name: string;
  description: string;
  amount_minor: number;
  amount_display: string;
  currency: "INR";
  billing_type: "one_time";
  duration_days: number;
  entitlement_kind: "credit_bundle";
  analysis_units: number;
  job_service_credits: number;
  entitlement_quantity: number;
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
  availability_message?: string | null;
  service_prices?: { search_credits_per_job: number; apply_credits_per_job: number; pricing_version: string } | null;
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

  const controller = new AbortController();
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<null>((resolve) => {
    deadlineTimer = setTimeout(() => {
      controller.abort();
      resolve(null);
    }, 2_500);
  });

  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(`${apiBase}/public/billing/catalog`, {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) return null;

        const data: unknown = await response.json();
        return isCatalog(data) ? data : null;
      })(),
      deadline,
    ]);
  } catch {
    return null;
  } finally {
    if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
  }
}
