import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const state = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), track: vi.fn() }));
vi.mock("../../lib/api", () => ({
  apiGet: state.get,
  apiPostJson: state.post,
  ApiError: class extends Error {
    constructor(message: string, public status: number, public detail: unknown) { super(message); }
  },
}));
vi.mock("../../lib/analytics", () => ({ trackEvent: state.track }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("sku=starter_bundle"), usePathname: () => "/billing" }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: "authenticated", data: { user: { id: "billing-owner", email: "owner@example.test" } } }) }));

import { ApiError } from "../../lib/api";
import ActualBillingPage from "./page";
import Nav from "../../components/Nav";

const clients = new Set<QueryClient>();
function BillingPage({ navigation = false }: { navigation?: boolean }) {
  const [client] = useState(() => {
    const value = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.add(value);
    return value;
  });
  return <QueryClientProvider client={client}>{navigation ? <Nav /> : null}<ActualBillingPage /></QueryClientProvider>;
}

type AcceptedQuote = {
  catalog_version: string;
  amount_minor: number;
  currency: string;
  job_service_credits: number;
  analysis_units: number;
};

const originalQuote: AcceptedQuote = {
  catalog_version: "fixture-v1", amount_minor: 64900, currency: "INR",
  job_service_credits: 100, analysis_units: 2,
};
const openCheckout = vi.fn();
const hostedCheckout = vi.fn(function (options: unknown) { void options; return { open: openCheckout, on: vi.fn() }; });

function catalog(quote = originalQuote) {
  return {
    catalog_version: quote.catalog_version, market: "IN", checkout_enabled: true, provider: "razorpay",
    products: [{
      sku: "starter_bundle", name: "Starter", description: "Finite prepaid test pack",
      amount_minor: quote.amount_minor, amount_display: "₹649", currency: quote.currency,
      billing_type: "one_time", duration_days: 0, entitlement_kind: "credit_bundle",
      entitlement_quantity: quote.job_service_credits, job_service_credits: quote.job_service_credits, analysis_units: quote.analysis_units,
      auto_renews: false, catalog_visible: true, enabled_for_purchase: true,
    }],
  };
}

function order(quote = originalQuote) {
  return {
    order_id: "order_original_fixture", provider: "razorpay", provider_order_id: "provider_order_fixture",
    key_id: "fixture-public-key", amount_minor: quote.amount_minor, currency: quote.currency,
    name: "HireWiz fixture", description: "Finite prepaid test pack", accepted_quote: { ...quote },
  };
}

function orderStatus(status: "pending" | "paid" | "refunded" = "pending", quote = originalQuote) {
  return {
    order_id: "order_original_fixture", provider_order_id: "provider_order_fixture", sku: "starter_bundle", status, fulfilled: status === "paid",
    checkout_available: status === "pending",
    amount_minor: quote.amount_minor, currency: quote.currency, accepted_quote: { ...quote },
    created_at: "2026-10-14T08:00:00Z", paid_at: status !== "pending" ? "2026-10-14T08:01:00Z" : null,
    refunded_at: status === "refunded" ? "2026-10-14T08:02:00Z" : null,
  };
}

function purchaseRequest(quote = originalQuote) {
  return { sku: "starter_bundle", billing_country: "IN", accepted_quote: quote, idempotency_key: expect.any(String) };
}

function read(url: string) {
  if (url === "/public/billing/catalog") return Promise.resolve(catalog());
  if (url === "/auth/profile") return Promise.resolve({ tier: "free", ai_credits: 0 });
  if (url === "/auth/me") return Promise.resolve({ tier: "free", ai_credits: 0, job_service_credits: 0 });
  if (url === "/v1/usage-events?limit=20") return Promise.resolve({ balance: 0, items: [] });
  if (url === "/billing/recent-order") return Promise.resolve(null);
  if (url === "/v1/employer-jobs/credits?limit=10") return Promise.resolve({ balance: 0, reserved_credits: 0, reservations: [], operations: [], items: [], next_cursor: null });
  throw new Error(`Unexpected billing fixture GET: ${url}`);
}

async function reviewPurchase() {
  render(<BillingPage />);
  const confirmation = await screen.findByRole("checkbox", { name: /I confirm my billing country/ });
  await waitFor(() => expect(confirmation).toBeEnabled());
  fireEvent.click(confirmation);
  const pay = screen.getByRole("button", { name: /Pay with Razorpay/ });
  await waitFor(() => expect(pay).toBeEnabled());
  return { confirmation, pay };
}

function expectNoHostedCheckout() {
  expect(hostedCheckout).not.toHaveBeenCalled();
  expect(openCheckout).not.toHaveBeenCalled();
  expect(document.querySelector('script[src="https://checkout.razorpay.com/v1/checkout.js"]')).toBeNull();
}

beforeEach(() => {
  state.get.mockReset().mockImplementation(read);
  state.post.mockReset();
  state.track.mockReset();
  hostedCheckout.mockClear();
  openCheckout.mockClear();
  window.Razorpay = hostedCheckout as unknown as NonNullable<Window["Razorpay"]>;
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.clear(); delete window.Razorpay; });

describe("billing account balance", () => {
  it("shares Nav's profile read and joins its single refresh after verified payment status", async () => {
    let profileReads = 0;
    state.get.mockImplementation((url: string) => {
      if (url === "/auth/profile") return Promise.resolve({ tier: "free", ai_credits: ++profileReads === 1 ? 7 : 9 });
      if (url === "/v1/features") return Promise.resolve({ features: {} });
      if (url === "/billing/recent-order") return Promise.resolve(orderStatus());
      if (url === "/billing/orders/order_original_fixture") return Promise.resolve(orderStatus("paid"));
      return read(url);
    });
    render(<BillingPage navigation />);
    await screen.findByTitle("7 analysis units remaining");
    const check = await screen.findByRole("button", { name: "Check payment status" });
    expect(profileReads).toBe(1);
    fireEvent.click(check);
    await screen.findByRole("heading", { name: "Payment confirmed" });
    await screen.findByTitle("9 analysis units remaining");
    expect(profileReads).toBe(2);
    expect(state.post).not.toHaveBeenCalled();
  });
  it("matches the authoritative account and ledger balance when profile omits service credits", async () => {
    let resolveAccount!: (account: { job_service_credits: number }) => void;
    const account = new Promise<{ job_service_credits: number }>((resolve) => { resolveAccount = resolve; });
    state.get.mockImplementation((url: string) => {
      if (url === "/auth/profile") return Promise.resolve({ tier: "premium", ai_credits: 7, premium_until: "2026-11-01T00:00:00Z" });
      if (url === "/auth/me") return account;
      if (url === "/v1/employer-jobs/credits?limit=10") return Promise.resolve({ balance: 3, reserved_credits: 0, reservations: [], operations: [], items: [], next_cursor: null });
      return read(url);
    });

    render(<BillingPage />);
    await screen.findByText("Premium active");
    const entitlement = screen.getByRole("region", { name: "Current entitlement" });
    const balance = within(entitlement).getByText("Job service credits").parentElement!;
    expect(within(balance).getByText("...")).toBeInTheDocument();
    expect(within(balance).queryByText("0")).not.toBeInTheDocument();
    expect(await within(screen.getByRole("region", { name: "Service history" })).findByText("3")).toBeInTheDocument();

    await act(async () => { resolveAccount({ job_service_credits: 3 }); });
    expect(within(balance).getByText("3")).toBeInTheDocument();
    expect(within(balance).queryByText("0")).not.toBeInTheDocument();
    expect(state.get).toHaveBeenCalledWith("/auth/me");
    expect(state.post).not.toHaveBeenCalled();
  });

  it.each([0, 3])("shows a verified balance of %s independently of unavailable profile metadata", async (balance) => {
    state.get.mockImplementation((url: string) => url === "/auth/profile" ? Promise.reject(new Error("Profile unavailable"))
      : url === "/auth/me" ? Promise.resolve({ job_service_credits: balance }) : read(url));
    render(<BillingPage />);
    const card = within(screen.getByRole("region", { name: "Current entitlement" })).getByText("Job service credits").parentElement!;
    expect(await within(card).findByText(String(balance))).toBeInTheDocument();
    expect(within(screen.getByText("Current access").parentElement!).getByText("Checking status")).toBeInTheDocument();
  });

  it.each([undefined, null, "3", -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])("does not invent zero for an invalid account balance (%s)", async (balance) => {
    state.get.mockImplementation((url: string) => url === "/auth/me" ? Promise.resolve({ job_service_credits: balance }) : read(url));
    render(<BillingPage />);
    const card = within(screen.getByRole("region", { name: "Current entitlement" })).getByText("Job service credits").parentElement!;
    expect(await within(card).findByText("Unavailable")).toBeInTheDocument();
    expect(within(card).queryByText("0")).not.toBeInTheDocument();
    expect(within(card).getByText(/Refresh this page to try again/)).toBeInTheDocument();
  });

  it("shows unavailable when the authoritative account read fails", async () => {
    state.get.mockImplementation((url: string) => url === "/auth/me" ? Promise.reject(new Error("Account unavailable")) : read(url));
    render(<BillingPage />);
    const card = within(screen.getByRole("region", { name: "Current entitlement" })).getByText("Job service credits").parentElement!;
    expect(await within(card).findByText("Unavailable")).toBeInTheDocument();
    expect(within(card).queryByText("0")).not.toBeInTheDocument();
    expect(state.post).not.toHaveBeenCalled();
  });
});

describe("billing checkout quote acceptance", () => {
  it.each(["legacy", "missing allowance", "inconsistent allowance", "duplicate SKU"])("never starts checkout for a catalog containing only %s offers", async (failure) => {
    const response = catalog();
    const product = response.products[0];
    const products = failure === "legacy" ? [{ ...product, entitlement_kind: "premium_access" }]
      : failure === "missing allowance" ? [{ ...product, job_service_credits: undefined }]
        : failure === "inconsistent allowance" ? [{ ...product, job_service_credits: 99 }]
          : [product, { ...product, amount_minor: 69900 }];
    state.get.mockImplementation((url: string) => url === "/public/billing/catalog"
      ? Promise.resolve({ ...response, products }) : read(url));

    render(<BillingPage />);
    await screen.findByText("No paid product is currently available.");
    expect(screen.queryByRole("button", { name: /Pay with Razorpay/ })).not.toBeInTheDocument();
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
  });

  it("requires a boolean checkout decision rather than a truthy string", async () => {
    state.get.mockImplementation((url: string) => url === "/public/billing/catalog"
      ? Promise.resolve({ ...catalog(), checkout_enabled: "true" }) : read(url));
    render(<BillingPage />);
    expect(await screen.findByRole("button", { name: "Checkout unavailable" })).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
  });

  it.each([
    { label: "service credits", job_service_credits: 90, analysis_units: 2 },
    { label: "analysis units", job_service_credits: 100, analysis_units: 1 },
  ])("requires fresh review when $label change at the same INR price", async (entitlements) => {
    const refreshedQuote = { ...originalQuote, catalog_version: "fixture-v2", job_service_credits: entitlements.job_service_credits, analysis_units: entitlements.analysis_units };
    let catalogReads = 0;
    state.get.mockImplementation((url: string) => url === "/public/billing/catalog"
      ? Promise.resolve(catalog(++catalogReads === 1 ? originalQuote : refreshedQuote)) : read(url));
    state.post.mockRejectedValueOnce(new ApiError("The purchase quote changed. Review the refreshed terms.", 409, { detail: { code: "billing_quote_requires_review" } }))
      .mockResolvedValueOnce(order(refreshedQuote));

    const { pay } = await reviewPurchase();
    fireEvent.click(pay);
    await screen.findByRole("alert");
    await screen.findByText(`${refreshedQuote.job_service_credits} service credits + ${refreshedQuote.analysis_units} analysis units after verified payment`);
    expect(catalogReads).toBe(2);
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0]).toEqual(["/billing/orders", purchaseRequest()]);
    expectNoHostedCheckout();

    const confirmation = screen.getByRole("checkbox", { name: /I confirm my billing country/ });
    const renewedPay = screen.getByRole("button", { name: /Pay with Razorpay/ });
    expect(confirmation).not.toBeChecked();
    expect(renewedPay).toBeDisabled();
    fireEvent.click(renewedPay);
    expect(state.post).toHaveBeenCalledTimes(1);
    fireEvent.click(confirmation);
    expect(renewedPay).toBeEnabled();
    expect(state.post).toHaveBeenCalledTimes(1);
    fireEvent.click(renewedPay);
    await waitFor(() => expect(openCheckout).toHaveBeenCalledTimes(1));
    expect(state.post.mock.calls[1]).toEqual(["/billing/orders", purchaseRequest(refreshedQuote)]);
    expect(state.post.mock.calls[1][1].idempotency_key).not.toBe(state.post.mock.calls[0][1].idempotency_key);
  });

  it.each([
    { catalog_version: "different-version" },
    { amount_minor: 65000 },
    { currency: "USD" },
    { job_service_credits: 99 },
    { analysis_units: 1 },
  ])("blocks hosted checkout when the accepted order quote differs: %j", async (change) => {
    state.post.mockResolvedValue({ ...order(), accepted_quote: { ...originalQuote, ...change } });
    const { pay } = await reviewPurchase();
    fireEvent.click(pay);
    await screen.findByRole("alert");
    expect(state.post).toHaveBeenCalledTimes(1);
    expectNoHostedCheckout();
    expect(screen.queryByRole("heading", { name: "Payment confirmed" })).not.toBeInTheDocument();
  });

  it("opens hosted checkout only for the exact accepted amount and entitlements", async () => {
    state.post.mockResolvedValue(order());
    const { pay } = await reviewPurchase();
    fireEvent.click(pay);
    await waitFor(() => expect(openCheckout).toHaveBeenCalledTimes(1));
    expect(state.post.mock.calls[0]).toEqual(["/billing/orders", purchaseRequest()]);
    expect(hostedCheckout.mock.calls[0][0]).toMatchObject({ amount: 64900, currency: "INR", order_id: "provider_order_fixture" });
    expect(screen.queryByRole("heading", { name: "Payment confirmed" })).not.toBeInTheDocument();
  });

  it("requires an explicit retry after a lost response and accepts the original order quote", async () => {
    state.post.mockRejectedValueOnce(new Error("The order response was interrupted."))
      .mockResolvedValueOnce(order());
    const { pay } = await reviewPurchase();
    fireEvent.click(pay);
    await screen.findByRole("alert");
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0]).toEqual(["/billing/orders", purchaseRequest()]);
    expectNoHostedCheckout();
    const retry = screen.getByRole("button", { name: /Pay with Razorpay/ });
    await waitFor(() => expect(retry).toBeEnabled());
    fireEvent.click(retry);
    await waitFor(() => expect(openCheckout).toHaveBeenCalledTimes(1));
    expect(state.post.mock.calls[1]).toEqual(state.post.mock.calls[0]);
    expect(state.get.mock.calls.filter(([url]) => url === "/public/billing/catalog")).toHaveLength(1);
    expect(screen.getByText("Order reference: order_original_fixture")).toBeInTheDocument();
  });

  it("recovers a captured original order after a lost create response without opening another checkout", async () => {
    state.post.mockRejectedValueOnce(new Error("The order response was interrupted."))
      .mockRejectedValueOnce(new ApiError("Read this original order's current status.", 409, {
        detail: { code: "billing_order_requires_status", order_id: "order_original_fixture" },
      }));
    state.get.mockImplementation((url: string) => url === "/billing/orders/order_original_fixture"
      ? Promise.resolve(orderStatus("paid")) : read(url));

    const { pay } = await reviewPurchase();
    fireEvent.click(pay);
    await screen.findByRole("alert");
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0]).toEqual(["/billing/orders", purchaseRequest()]);
    expectNoHostedCheckout();
    const retry = screen.getByRole("button", { name: /Pay with Razorpay/ });
    await waitFor(() => expect(retry).toBeEnabled());
    fireEvent.click(retry);

    await screen.findByRole("heading", { name: "Payment confirmed" });
    expect(state.post).toHaveBeenCalledTimes(2);
    expect(state.post.mock.calls[1]).toEqual(state.post.mock.calls[0]);
    expect(state.get.mock.calls.filter(([url]) => url === "/billing/orders/order_original_fixture")).toHaveLength(1);
    expect(state.get.mock.calls.filter(([url]) => url === "/public/billing/catalog")).toHaveLength(1);
    expect(screen.getByText("Order reference: order_original_fixture")).toBeInTheDocument();
    expectNoHostedCheckout();
  });

  it("shows a known pending order's original terms and resumes only that reviewed checkout", async () => {
    const currentQuote = { ...originalQuote, catalog_version: "fixture-v2", amount_minor: 69900, job_service_credits: 120, analysis_units: 3 };
    state.get.mockImplementation((url: string) => url === "/public/billing/catalog"
      ? Promise.resolve(catalog(currentQuote)) : url === "/billing/recent-order"
        ? Promise.resolve(orderStatus()) : read(url));
    state.post.mockResolvedValue(order());

    render(<BillingPage />);
    const resume = await screen.findByRole("button", { name: "Resume original checkout" });
    const pay = screen.getByRole("button", { name: /Pay with Razorpay/ });
    expect(pay).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
    expect(screen.getByLabelText("Original checkout terms")).toHaveTextContent("Original order: 100 service credits + 2 analysis units for ₹649.");

    const confirmation = screen.getByRole("checkbox", { name: /I confirm my billing country/ });
    expect(confirmation).not.toBeChecked();
    fireEvent.click(confirmation);
    expect(pay).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
    fireEvent.click(resume);
    await waitFor(() => expect(openCheckout).toHaveBeenCalledTimes(1));
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0]).toEqual(["/billing/orders", { ...purchaseRequest(), order_reference: "order_original_fixture" }]);
    expect(hostedCheckout.mock.calls[0][0]).toMatchObject({ amount: 64900, currency: "INR", order_id: "provider_order_fixture" });
  });

  it("does not resume a legacy pending order without its original accepted quote", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order"
      ? Promise.resolve({ ...orderStatus(), accepted_quote: undefined }) : read(url));
    render(<BillingPage />);
    await screen.findByText("Order reference: order_original_fixture");
    const confirmation = await screen.findByRole("checkbox", { name: /I confirm my billing country/ });
    fireEvent.click(confirmation);
    expect(screen.getByRole("button", { name: /Pay with Razorpay/ })).toBeDisabled();
    const resume = screen.queryByRole("button", { name: "Resume original checkout" });
    if (resume) expect(resume).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
  });

  it("keeps an unknown checkout creation unpayable while allowing free original status checks", async () => {
    const unknownCreation = { ...orderStatus(), provider_order_id: null, checkout_available: false };
    state.get.mockImplementation((url: string) => ["/billing/recent-order", "/billing/orders/order_original_fixture"].includes(url)
      ? Promise.resolve(unknownCreation) : read(url));
    render(<BillingPage />);
    await screen.findByText("Order reference: order_original_fixture");
    const unknownMessage = screen.getByText(/creation.*unknown|unknown.*creation/i);
    expect(unknownMessage).toHaveTextContent(/support/i);
    const confirmation = await screen.findByRole("checkbox", { name: /I confirm my billing country/ });
    fireEvent.click(confirmation);
    const pay = screen.getByRole("button", { name: /Pay with Razorpay/ });
    const resume = screen.getByRole("button", { name: "Resume original checkout" });
    expect(pay).toBeDisabled();
    expect(resume).toBeDisabled();
    fireEvent.click(pay);
    fireEvent.click(resume);
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();

    const checkStatus = screen.getByRole("button", { name: "Check payment status" });
    expect(checkStatus).toBeEnabled();
    fireEvent.click(checkStatus);
    await waitFor(() => expect(state.get.mock.calls.filter(([url]) => url === "/billing/orders/order_original_fixture")).toHaveLength(1));
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
  });

  it("reconciles an unknown original order and requires fresh acceptance before a separate resume", async () => {
    const currentQuote = { ...originalQuote, catalog_version: "fixture-v2", amount_minor: 69900, job_service_credits: 120, analysis_units: 3 };
    const unknownCreation = { ...orderStatus(), provider_order_id: null, accepted_quote: null, checkout_available: false };
    state.get.mockImplementation((url: string) => url === "/public/billing/catalog"
      ? Promise.resolve(catalog(currentQuote)) : url === "/billing/recent-order"
        ? Promise.resolve(unknownCreation) : read(url));
    state.post.mockResolvedValueOnce({
      resolution: "checkout_ready", message: "The original checkout is ready for review.", order: orderStatus(),
    }).mockResolvedValueOnce(order());

    render(<BillingPage />);
    const reconcile = await screen.findByRole("button", { name: "Reconcile original payment" });
    const confirmation = await screen.findByRole("checkbox", { name: /I confirm my billing country/ });
    await waitFor(() => expect(confirmation).toBeEnabled());
    fireEvent.click(confirmation);
    expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
    fireEvent.click(reconcile);

    await screen.findByLabelText("Original checkout terms");
    expect(screen.getByLabelText("Original checkout terms")).toHaveTextContent("Original order: 100 service credits + 2 analysis units for ₹649.");
    expect(state.post.mock.calls).toEqual([["/billing/orders/order_original_fixture/reconcile", undefined]]);
    expect(confirmation).not.toBeChecked();
    const resume = screen.getByRole("button", { name: "Resume original checkout" });
    expect(resume).toBeDisabled();
    expect(screen.getByRole("button", { name: /Pay with Razorpay/ })).toBeDisabled();
    expectNoHostedCheckout();
    fireEvent.click(resume);
    expect(state.post).toHaveBeenCalledTimes(1);
    fireEvent.click(confirmation);
    expect(resume).toBeEnabled();
    expect(state.post).toHaveBeenCalledTimes(1);
    expectNoHostedCheckout();
    fireEvent.click(resume);

    await waitFor(() => expect(openCheckout).toHaveBeenCalledTimes(1));
    expect(state.post).toHaveBeenCalledTimes(2);
    expect(state.post.mock.calls[1]).toEqual(["/billing/orders", { ...purchaseRequest(), order_reference: "order_original_fixture" }]);
    expect(hostedCheckout.mock.calls[0][0]).toMatchObject({ amount: 64900, currency: "INR", order_id: "provider_order_fixture" });
  });

  it("keeps unresolved reconciliation unpayable across free status reads even with a provider order ID", async () => {
    const unresolvedOrder = { ...orderStatus(), checkout_available: false };
    state.get.mockImplementation((url: string) => ["/billing/recent-order", "/billing/orders/order_original_fixture"].includes(url)
      ? Promise.resolve(unresolvedOrder) : read(url));
    const unresolvedMessage = "The original payment is still unresolved. Review it with billing support before paying.";
    state.post.mockResolvedValue({ resolution: "unresolved", message: unresolvedMessage, order: unresolvedOrder });

    render(<BillingPage />);
    const reconcile = await screen.findByRole("button", { name: "Reconcile original payment" });
    const confirmation = await screen.findByRole("checkbox", { name: /I confirm my billing country/ });
    await waitFor(() => expect(confirmation).toBeEnabled());
    fireEvent.click(confirmation);
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
    fireEvent.click(reconcile);

    await screen.findByText(unresolvedMessage);
    expect(state.post.mock.calls).toEqual([["/billing/orders/order_original_fixture/reconcile", undefined]]);
    if (!(confirmation as HTMLInputElement).checked) fireEvent.click(confirmation);
    expect(confirmation).toBeChecked();
    expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Pay with Razorpay/ })).toBeDisabled();
    expectNoHostedCheckout();

    vi.useFakeTimers();
    try {
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Check payment status" }));
        await vi.runAllTimersAsync();
      });
    } finally {
      vi.useRealTimers();
    }
    expect(state.get.mock.calls.filter(([url]) => url === "/billing/orders/order_original_fixture").length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: "Check payment status" })).toBeEnabled();
    expect(confirmation).toBeChecked();
    const resume = screen.getByRole("button", { name: "Resume original checkout" });
    expect(resume).toBeDisabled();
    fireEvent.click(resume);
    expect(state.post).toHaveBeenCalledTimes(1);
    expectNoHostedCheckout();
  });

  it("allows purchase review after a free status check confirms a previously unresolved original order was refunded", async () => {
    const unresolvedOrder = { ...orderStatus(), checkout_available: false };
    const unresolvedMessage = "The original payment is still unresolved. Review it with billing support before paying.";
    state.get.mockImplementation((url: string) => url === "/billing/recent-order"
      ? Promise.resolve(unresolvedOrder) : url === "/billing/orders/order_original_fixture"
        ? Promise.resolve(orderStatus("refunded")) : read(url));
    state.post.mockResolvedValue({ resolution: "unresolved", message: unresolvedMessage, order: unresolvedOrder });

    render(<BillingPage />);
    const reconcile = await screen.findByRole("button", { name: "Reconcile original payment" });
    expect(state.post).not.toHaveBeenCalled();
    fireEvent.click(reconcile);
    await screen.findByText(unresolvedMessage);
    expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled();
    expectNoHostedCheckout();
    fireEvent.click(screen.getByRole("button", { name: "Check payment status" }));

    const review = await screen.findByRole("button", { name: "Review another purchase" });
    expect(review).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/refunded/i);
    expect(screen.queryByRole("heading", { name: "Payment confirmed" })).not.toBeInTheDocument();
    expect(state.get.mock.calls.filter(([url]) => url === "/billing/orders/order_original_fixture")).toHaveLength(1);
    expect(state.post.mock.calls).toEqual([["/billing/orders/order_original_fixture/reconcile", undefined]]);
    expectNoHostedCheckout();
  });

  it("keeps a reloaded pending order unpayable when checkout is unavailable despite its provider ID and accepted quote", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order"
      ? Promise.resolve({ ...orderStatus(), checkout_available: false }) : read(url));

    render(<BillingPage />);
    await screen.findByText("Order reference: order_original_fixture");
    expect(screen.getByLabelText("Original checkout terms")).toHaveTextContent("Original order: 100 service credits + 2 analysis units for ₹649.");
    const confirmation = await screen.findByRole("checkbox", { name: /I confirm my billing country/ });
    await waitFor(() => expect(confirmation).toBeEnabled());
    fireEvent.click(confirmation);
    expect(confirmation).toBeChecked();
    const resume = screen.getByRole("button", { name: "Resume original checkout" });
    const pay = screen.getByRole("button", { name: /Pay with Razorpay/ });
    expect(resume).toBeDisabled();
    expect(pay).toBeDisabled();
    fireEvent.click(resume);
    fireEvent.click(pay);
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
  });

  it("confirms a settled paid original order through reconciliation without opening checkout", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order"
      ? Promise.resolve({ ...orderStatus(), provider_order_id: null, checkout_available: false }) : read(url));
    state.post.mockResolvedValue({ resolution: "settled", message: "Verified payment was settled.", order: orderStatus("paid") });

    render(<BillingPage />);
    const reconcile = await screen.findByRole("button", { name: "Reconcile original payment" });
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
    fireEvent.click(reconcile);

    await screen.findByRole("heading", { name: "Payment confirmed" });
    expect(state.post.mock.calls).toEqual([["/billing/orders/order_original_fixture/reconcile", undefined]]);
    expect(screen.getByText("Order reference: order_original_fixture")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume original checkout" })).not.toBeInTheDocument();
    expectNoHostedCheckout();
  });

  it("shows a settled refunded order for review without confirming or creating another checkout", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order"
      ? Promise.resolve({ ...orderStatus(), provider_order_id: null, checkout_available: false }) : read(url));
    state.post.mockResolvedValue({ resolution: "settled", message: "The original payment was refunded.", order: orderStatus("refunded") });

    render(<BillingPage />);
    const reconcile = await screen.findByRole("button", { name: "Reconcile original payment" });
    expect(state.post).not.toHaveBeenCalled();
    expectNoHostedCheckout();
    fireEvent.click(reconcile);

    const review = await screen.findByRole("button", { name: "Review another purchase" });
    expect(screen.getByRole("alert")).toHaveTextContent(/refunded/i);
    expect(screen.queryByRole("heading", { name: "Payment confirmed" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume original checkout" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay with Razorpay/ })).toBeDisabled();
    expect(state.post.mock.calls).toEqual([["/billing/orders/order_original_fixture/reconcile", undefined]]);
    expectNoHostedCheckout();
    fireEvent.click(review);
    expect(screen.getByRole("checkbox", { name: /I confirm my billing country/ })).not.toBeChecked();
    expect(screen.getByRole("button", { name: /Pay with Razorpay/ })).toBeDisabled();
    expect(state.post).toHaveBeenCalledTimes(1);
    expectNoHostedCheckout();
  });

  it("retains the lost-response purchase identity through status, reconciliation and explicit original resume", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/orders/order_original_fixture"
      ? Promise.resolve({ ...orderStatus(), provider_order_id: null, checkout_available: false }) : read(url));
    state.post.mockRejectedValueOnce(new Error("The order response was interrupted."))
      .mockRejectedValueOnce(new ApiError("Read this original order's current status.", 409, {
        detail: { code: "billing_order_requires_status", order_id: "order_original_fixture" },
      }))
      .mockResolvedValueOnce({ resolution: "checkout_ready", message: "The original checkout is ready for review.", order: orderStatus() })
      .mockResolvedValueOnce(order());

    const { pay, confirmation } = await reviewPurchase();
    fireEvent.click(pay);
    await screen.findByRole("alert");
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0]).toEqual(["/billing/orders", purchaseRequest()]);
    const originalRequest = state.post.mock.calls[0][1];
    expectNoHostedCheckout();
    await waitFor(() => expect(pay).toBeEnabled());
    fireEvent.click(pay);

    await screen.findByText("Order reference: order_original_fixture");
    expect(state.post).toHaveBeenCalledTimes(2);
    expect(state.post.mock.calls[1]).toEqual(state.post.mock.calls[0]);
    expect(state.get.mock.calls.filter(([url]) => url === "/billing/orders/order_original_fixture")).toHaveLength(1);
    expectNoHostedCheckout();
    fireEvent.click(screen.getByRole("button", { name: "Reconcile original payment" }));

    await screen.findByText("The original checkout is ready for review.");
    expect(confirmation).not.toBeChecked();
    expect(state.post).toHaveBeenCalledTimes(3);
    expect(state.post.mock.calls[2]).toEqual(["/billing/orders/order_original_fixture/reconcile", undefined]);
    const resume = screen.getByRole("button", { name: "Resume original checkout" });
    expect(resume).toBeDisabled();
    expectNoHostedCheckout();
    fireEvent.click(confirmation);
    expect(resume).toBeEnabled();
    expect(state.post).toHaveBeenCalledTimes(3);
    fireEvent.click(resume);

    await waitFor(() => expect(openCheckout).toHaveBeenCalledTimes(1));
    expect(state.post).toHaveBeenCalledTimes(4);
    expect(state.post.mock.calls[3]).toEqual(["/billing/orders", { ...originalRequest, order_reference: "order_original_fixture" }]);
    expect(hostedCheckout.mock.calls[0][0]).toMatchObject({ amount: 64900, currency: "INR", order_id: "provider_order_fixture" });
    expect(state.get.mock.calls.filter(([url]) => url === "/public/billing/catalog")).toHaveLength(1);
  });
});
