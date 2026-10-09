import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("server-only", () => ({}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("sku=job_service_500") }));
vi.mock("../../lib/api", () => ({ apiGet: vi.fn(), apiPostJson: vi.fn() }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
vi.mock("../../lib/billingCatalog", () => ({ getPublicBillingCatalog: vi.fn() }));
vi.mock("../../components/ui/FadeIn", () => ({ default: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("../../components/ui/PageHeader", () => ({ default: ({ title, subtitle }: { title: string; subtitle: string }) => <header><h1>{title}</h1><p>{subtitle}</p></header> }));

import { apiGet, apiPostJson } from "../../lib/api";
import { getPublicBillingCatalog } from "../../lib/billingCatalog";
import { finiteCreditBundles } from "../../lib/billingProducts";
import BillingPage from "./page";
import PricingPage from "../pricing/page";
import DashboardPage from "../dashboard/page";

const bundle = {
  sku: "starter_bundle", name: "Server Starter", description: "Finite server balances",
  amount_minor: 87654, amount_display: "₹876.54", currency: "INR", billing_type: "one_time",
  duration_days: 0, entitlement_kind: "credit_bundle", entitlement_quantity: 99,
  analysis_units: 2, job_service_credits: 99, auto_renews: false,
  catalog_visible: true, enabled_for_purchase: true,
};
const historical = [
  { ...bundle, sku: "premium_30d", name: "Retired unlimited pass", entitlement_kind: "premium_access" },
  { ...bundle, sku: "job_service_500", name: "Retired cheap credits", entitlement_kind: "job_service_credits" },
];
const order = {
  order_id: "accepted_order_fixture", payment_reference: "verified_reference_fixture", sku: "premium_30d",
  status: "paid", fulfilled: true, amount_minor: 99900, currency: "INR",
  created_at: "2026-10-08T10:00:00Z", paid_at: "2026-10-08T10:00:01Z", refunded_at: null,
};
let products: unknown[];
let checkoutEnabled: boolean;
let recentOrder: typeof order | null;
let premium: boolean;

beforeEach(() => {
  products = historical;
  checkoutEnabled = true;
  recentOrder = null;
  premium = true;
  vi.stubEnv("BACKEND_URL", "https://synthetic.example.test/api");
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(catalog())));
  vi.mocked(getPublicBillingCatalog).mockImplementation(async () => ({ ...catalog(), products: finiteCreditBundles(products) }));
  vi.mocked(apiGet).mockImplementation(async (path) => {
    if (path === "/public/billing/catalog") return catalog();
    if (path === "/auth/profile") return { tier: premium ? "premium" : "free", ai_credits: 5, job_service_credits: 40, premium_until: "2026-11-08T10:00:00Z" };
    if (path === "/v1/usage-events?limit=20") return { balance: 5, items: [] };
    if (path === "/billing/recent-order") return recentOrder;
    if (path === `/billing/orders/${order.order_id}`) return { ...order, sku: "job_service_500" };
    if (path === "/analytics/summary") return { resume_count: 0, applications_count: 0 };
    if (path === "/v1/features") return { features: { career_workspace: { enabled: false } } };
    throw new Error(`Unexpected synthetic read: ${path}`);
  });
  vi.mocked(apiPostJson).mockRejectedValue(new Error("Synthetic checkout not performed"));
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function catalog() {
  return { catalog_version: "synthetic-current", market: "IN" as const, checkout_enabled: checkoutEnabled, provider: "razorpay" as const, products };
}

describe("retired offers and preserved accepted purchases", () => {
  it("old enabled backend catalog cannot start a new purchase, including an old SKU query", async () => {
    render(<BillingPage />);
    expect(await screen.findByText("Purchasing is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.getByText("Premium active")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.queryByText("Retired unlimited pass")).not.toBeInTheDocument();
    expect(screen.queryByText("Retired cheap credits")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pay with Razorpay|Review purchase/ })).not.toBeInTheDocument();
    expect(apiPostJson).not.toHaveBeenCalled();
    expect(document.querySelector('script[src="https://checkout.razorpay.com/v1/checkout.js"]')).toBeNull();
  });

  it("a server finite bundle replaces the old query selection and sends only its SKU after review", async () => {
    products = [...historical, bundle];
    render(<BillingPage />);
    const pay = await screen.findByRole("button", { name: /Pay with Razorpay.*876.54/ });
    expect(pay).toBeDisabled();
    expect(screen.getByText("2 AI analysis units")).toBeInTheDocument();
    expect(screen.getByText("99 job service credits")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /I confirm my billing country/ }));
    fireEvent.click(pay);
    await waitFor(() => expect(apiPostJson).toHaveBeenCalledWith("/billing/orders", { sku: "starter_bundle", billing_country: "IN" }));
    expect(await screen.findByText("Synthetic checkout not performed")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Payment confirmed" })).not.toBeInTheDocument();
  });

  it("a paused catalog cannot load hosted checkout or create an order", async () => {
    products = [bundle]; checkoutEnabled = false;
    render(<BillingPage />);
    expect(await screen.findByRole("button", { name: "Checkout unavailable" })).toBeDisabled();
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(apiPostJson).not.toHaveBeenCalled();
  });

  it("an invalid truthy checkout flag cannot make a finite product purchasable", async () => {
    products = [bundle];
    vi.mocked(apiGet).mockResolvedValueOnce({ ...catalog(), checkout_enabled: "true" });
    render(<BillingPage />);
    expect(await screen.findByRole("button", { name: "Checkout unavailable" })).toBeDisabled();
    expect(apiPostJson).not.toHaveBeenCalled();
  });

  it("keeps the verified historical Premium receipt and continuation available", async () => {
    recentOrder = order;
    render(<BillingPage />);
    expect(await screen.findByRole("heading", { name: "Payment confirmed" })).toBeInTheDocument();
    expect(screen.getByText(/delivered your purchased Premium access/)).toBeInTheDocument();
    expect(screen.getByText(/accepted_order_fixture/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue to dashboard" })).toHaveAttribute("href", "/dashboard");
    expect(apiPostJson).not.toHaveBeenCalled();
  });

  it("keeps pending historical credit-pack status reconciliation when purchases are unavailable", async () => {
    recentOrder = { ...order, sku: "job_service_500", status: "pending", fulfilled: false };
    render(<BillingPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Check payment status" }));
    expect(await screen.findByRole("heading", { name: "Payment confirmed" })).toBeInTheDocument();
    expect(screen.getByText(/delivered your purchased job service credits/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue to employer jobs" })).toHaveAttribute("href", "/employer-jobs");
    expect(apiPostJson).not.toHaveBeenCalled();
  });

  it("public pricing hides old enabled offers and publishes no replacement price", async () => {
    render(await PricingPage());
    expect(screen.getByRole("heading", { name: "Purchasing is temporarily unavailable" })).toBeInTheDocument();
    expect(screen.queryByText("Retired unlimited pass")).not.toBeInTheDocument();
    expect(screen.queryByText("Retired cheap credits")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Review this bundle" })).not.toBeInTheDocument();
    expect(screen.queryByText(/₹999|Unlimited/)).not.toBeInTheDocument();
  });

  it("public pricing uses only the exact finite catalog price and review destination", async () => {
    products = [...historical, bundle];
    render(await PricingPage());
    expect(screen.getByText("₹876.54")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review this bundle" })).toHaveAttribute("href", "/billing?sku=starter_bundle");
    expect(screen.queryByText("Retired cheap credits")).not.toBeInTheDocument();
  });

  it("dashboard does not turn the first legacy catalog product into a low-unit offer", async () => {
    premium = false;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><DashboardPage /></QueryClientProvider>);
    await screen.findByRole("heading", { name: /Good to see you/ });
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/public/billing/catalog"));
    expect(screen.queryByRole("link", { name: /Review Premium|Review credit bundle/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Retired unlimited pass")).not.toBeInTheDocument();
    client.clear();
  });

  it("dashboard quotes finite balances and the exact server price rather than access days", async () => {
    premium = false; products = [...historical, bundle];
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><DashboardPage /></QueryClientProvider>);
    expect(await screen.findByRole("link", { name: "Review credit bundle" })).toHaveAttribute("href", "/billing");
    expect(screen.getByText(/₹876.54 for 2 analysis units and 99 job service credits/)).toBeInTheDocument();
    expect(screen.queryByText(/99 days/)).not.toBeInTheDocument();
    client.clear();
  });
});
