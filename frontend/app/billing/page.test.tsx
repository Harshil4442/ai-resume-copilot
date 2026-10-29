const eligibleServices = [{ operation: "resume_tailor", credits: 8, deliverable: "Tailored resume with preserved source layout.", supported_limits: { max_pages: 2 }, source_formats: ["pdf", "docx"], route_version: "reviewed-v1" }];
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { RazorpayOptions } from "../../components/billing/hostedCheckout";

const state = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../../lib/api", () => ({ apiGet: state.get, apiPostJson: state.post,
  ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } },
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("sku=starter_pass") }));
import { ApiError } from "../../lib/api";
import ActualBillingPage from "./page";

const clients = new Set<QueryClient>();
function BillingPage() {
  const [client] = useState(() => { const value = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); clients.add(value); return value; });
  return <QueryClientProvider client={client}><ActualBillingPage /></QueryClientProvider>;
}
const schema = "unified-credits-v1";
const stamp = "2026-10-27T10:00:00Z";
const campaign = { id: "launch", version: "1", starts_at: "2026-10-01T00:00:00Z", ends_at: "2026-10-31T00:00:00Z", timezone: "Asia/Kolkata", status: "active", standard_amount_minor: 7900, reference_price_verified: false };
const pack = { sku: "starter_pass", name: "Starter", amount_minor: 5900, credits: 40, duration_days: 30, auto_renews: false, purchase_enabled: true, terms_version: "terms-v1", campaign };
const catalog = { schema_version: schema, eligible_services: eligibleServices, catalog_version: "price-v1", server_time: stamp, market: "IN", currency: "INR", checkout_enabled: true, products: [pack] };
const quote = { schema_version: schema, quote_id: "quote_fixture", sku: "starter_pass", name: "Starter", eligible_services: eligibleServices, catalog_version: "price-v1", terms_version: "terms-v1", amount_minor: 5900, currency: "INR", credits: 40, duration_days: 30, auto_renews: false, created_at: stamp, server_time: stamp, expires_at: "2026-10-27T10:15:00Z", pass_expires_at: "2026-11-26T10:00:00Z", campaign };
const wallet = { schema_version: schema, server_time: stamp, available_credits: 7, reserved_credits: 2, debt_credits: 0, lots: [{ grant_id: "grant_fixture", remaining_credits: 9, funding_kind: "paid", expires_at: "2026-11-26T10:00:00Z" }] };
function order(changes: Record<string, unknown> = {}) { return { schema_version: schema, order_id: "order_unified", provider_order_id: "provider_unified", status: "pending", payment_status: "created", fulfilled: false, checkout_available: true, amount_minor: 5900, refunded_amount_minor: 0, currency: "INR", sku: "starter_pass", created_at: stamp, paid_at: null, pass_expires_at: null, accepted_quote: quote, ...changes }; }
function checkout(changes: Record<string, unknown> = {}) { return { schema_version: schema, order_id: "order_unified", provider: "razorpay", provider_order_id: "provider_unified", key_id: "fixture-public-key", amount_minor: 5900, currency: "INR", name: "HireWiz", description: "40 credits; 30-day pass", accepted_quote: quote, prefill: { email: "owner@example.test" }, ...changes }; }
const legacyQuote = { catalog_version: "legacy-price", amount_minor: 64900, currency: "INR", job_service_credits: 100, analysis_units: 2 };
function legacyOrder(changes: Record<string, unknown> = {}) { return { order_id: "order_legacy", provider_order_id: "provider_legacy", sku: "starter_bundle", status: "pending", fulfilled: false, checkout_available: true, amount_minor: 64900, currency: "INR", accepted_quote: legacyQuote, ...changes }; }
const legacyCheckout = { order_id: "order_legacy", provider: "razorpay", provider_order_id: "provider_legacy", key_id: "fixture-public-key", amount_minor: 64900, currency: "INR", name: "HireWiz", description: "Original pack", accepted_quote: legacyQuote };
const openCheckout = vi.fn();
const hostedCheckout = vi.fn(function (options: RazorpayOptions) { void options; return { open: openCheckout, on: vi.fn() }; });
function read(url: string): Promise<unknown> {
  if (url === "/v1/credits/canary-enrollment") return Promise.resolve({ status: "NOT_AVAILABLE" });
  if (url === "/v1/credits/catalog") return Promise.resolve(catalog);
  if (url === "/v1/credits/wallet") return Promise.resolve(wallet);
  if (url === "/v1/credits/history?limit=20") return Promise.resolve({ schema_version: schema, items: [], next_cursor: null });
  if (url === "/v1/credits/recent-order" || url === "/billing/recent-order") return Promise.resolve(null);
  if (url === "/v1/credits/orders/order_unified") return Promise.resolve(order());
  if (url === "/billing/orders/order_legacy") return Promise.resolve(legacyOrder());
  if (url === "/auth/profile") return Promise.resolve({ tier: "free", ai_credits: 13 });
  if (url === "/auth/me") return Promise.resolve({ job_service_credits: 3 });
  if (url === "/v1/usage-events?limit=20") return Promise.resolve({ balance: 13, items: [] });
  if (url === "/v1/employer-jobs/credits?limit=10") return Promise.resolve({ balance: 3, reserved_credits: 0, reservations: [], operations: [], items: [], next_cursor: null });
  return Promise.reject(new Error(`Unexpected GET ${url}`));
}
function post(url: string): Promise<unknown> {
  if (url === "/v1/credits/purchase-quotes") return Promise.resolve(quote);
  if (url === "/v1/credits/orders") return Promise.resolve(checkout());
  return Promise.reject(new Error(`Unexpected POST ${url}`));
}
beforeEach(() => { state.get.mockReset().mockImplementation(read); state.post.mockReset().mockImplementation(post); hostedCheckout.mockClear(); openCheckout.mockClear(); window.Razorpay = hostedCheckout; });
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.clear(); delete window.Razorpay; vi.useRealTimers(); });
function noCheckout() { expect(hostedCheckout).not.toHaveBeenCalled(); expect(openCheckout).not.toHaveBeenCalled(); expect(document.querySelector('script[src="https://checkout.razorpay.com/v1/checkout.js"]')).toBeNull(); }
async function review() {
  render(<BillingPage />);
  const button = await screen.findByRole("button", { name: "Review Starter pass" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  return screen.findByRole("button", { name: "Continue with ₹59 payment" });
}
async function confirmIndia() { const checkbox = await screen.findByRole("checkbox", { name: /I confirm my billing country/ }); fireEvent.click(checkbox); }

describe("unified billing account", () => {
  it("shows a single new balance while preserving separate legacy entitlements", async () => {
    render(<BillingPage />);
    const available = await screen.findByText("Available credits");
    expect(within(available.parentElement!).getByText("7")).toBeInTheDocument();
    const legacy = screen.getByRole("region", { name: "Current entitlement" });
    expect(await within(legacy).findByText("13")).toBeInTheDocument(); expect(within(legacy).getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/have not been added together or converted/)).toBeInTheDocument();
    expect(screen.queryByText("20 credits")).not.toBeInTheDocument(); expect(state.post).not.toHaveBeenCalled();
  });
  it.each([undefined, null, -1, "3", 1.5])("does not invent a wallet balance for malformed data %j", async (available_credits) => {
    state.get.mockImplementation((url: string) => url === "/v1/credits/wallet" ? Promise.resolve({ ...wallet, available_credits }) : read(url));
    render(<BillingPage />); await screen.findByText(/Your credit balance is unavailable/); expect(screen.queryByText("Available credits")).not.toBeInTheDocument();
  });
  it("never falls back to selling old packs when unified pricing is unavailable", async () => {
    state.get.mockImplementation((url: string) => url === "/v1/credits/catalog" ? Promise.reject(new Error("Unavailable")) : read(url));
    render(<BillingPage />); await screen.findByRole("heading", { name: "Credit passes are currently unavailable" });
    expect(screen.queryByRole("button", { name: /Pay with Razorpay/ })).not.toBeInTheDocument();
    expect(state.get).not.toHaveBeenCalledWith("/public/billing/catalog"); expect(state.post).not.toHaveBeenCalled(); noCheckout();
  });
  it("honors the server's closed launch gate", async () => {
    state.get.mockImplementation((url: string) => url === "/v1/credits/catalog" ? Promise.resolve({ ...catalog, checkout_enabled: false }) : read(url));
    render(<BillingPage />); const review = await screen.findByRole("button", { name: "Review Starter pass" }); expect(review).toBeDisabled(); fireEvent.click(review); expect(state.post).not.toHaveBeenCalled(); noCheckout();
  });
  it.each(["/v1/credits/recent-order", "/billing/recent-order"])("waits for authoritative previous-purchase state when %s fails", async (failed) => {
    state.get.mockImplementation((url: string) => url === failed ? Promise.reject(new Error("Unavailable")) : read(url));
    render(<BillingPage />); await screen.findByText(failed.startsWith("/billing") ? /Could not verify previous purchases/ : /Could not verify your recent credit purchase/);
    expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeDisabled(); expect(state.post).not.toHaveBeenCalled(); noCheckout();
  });
});

describe("unified purchase review and payment", () => {
  it("requires an explicit quote review and India confirmation before order creation", async () => {
    const pay = await review(); expect(pay).toBeDisabled(); expect(state.post.mock.calls).toEqual([["/v1/credits/purchase-quotes", { sku: "starter_pass", idempotency_key: expect.any(String) }]]); noCheckout();
    await confirmIndia(); fireEvent.click(pay); await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
    expect(state.post.mock.calls[1]).toEqual(["/v1/credits/orders", { quote_id: quote.quote_id, billing_country: "IN", idempotency_key: expect.any(String) }]);
    expect(hostedCheckout.mock.calls[0][0]).toMatchObject({ amount: 5900, currency: "INR", order_id: "provider_unified" });
    expect(screen.queryByRole("heading", { name: "Credit payment confirmed" })).not.toBeInTheDocument();
  });
  it.each([{ amount_minor: 6000 }, { accepted_quote: { ...quote, credits: 41 } }, { accepted_quote: { ...quote, terms_version: "different" } }, { accepted_quote: { ...quote, pass_expires_at: "2026-11-25T10:00:00Z" } }])("blocks checkout when returned terms differ %j", async (change) => {
    state.post.mockImplementation((url: string) => url === "/v1/credits/orders" ? Promise.resolve(checkout(change)) : post(url));
    const pay = await review(); await confirmIndia(); fireEvent.click(pay); await screen.findByRole("alert"); noCheckout();
    expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeDisabled();
  });
  it("recovers a lost creation response using the exact same request identity", async () => {
    let creates = 0;
    state.post.mockImplementation((url: string) => url === "/v1/credits/orders" && ++creates === 1 ? Promise.reject(new Error("Connection interrupted")) : post(url));
    const pay = await review(); await confirmIndia(); fireEvent.click(pay);
    const recover = await screen.findByRole("button", { name: "Recover interrupted checkout" }); noCheckout(); fireEvent.click(recover);
    await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
    const createsOnly = state.post.mock.calls.filter(([url]) => url === "/v1/credits/orders"); expect(createsOnly).toHaveLength(2); expect(createsOnly[1]).toEqual(createsOnly[0]);
  });
  it("requires new review after a definite rejection that created no order", async () => {
    state.post.mockImplementation((url: string) => url === "/v1/credits/orders" ? Promise.reject(new ApiError("The quote expired. Review another quote.", 409, { detail: { code: "credit_quote_rejected", order_created: false } })) : post(url));
    const pay = await review(); await confirmIndia(); fireEvent.click(pay);
    await screen.findByText("The quote expired. Review another quote."); noCheckout();
    expect(screen.queryByRole("button", { name: "Recover interrupted checkout" })).not.toBeInTheDocument();
    const next = screen.getByRole("button", { name: "Review Starter pass" }); await waitFor(() => expect(next).toBeEnabled()); fireEvent.click(next);
    await screen.findByRole("button", { name: "Continue with ₹59 payment" });
    const quotes = state.post.mock.calls.filter(([url]) => url === "/v1/credits/purchase-quotes");
    expect(quotes).toHaveLength(2); expect(quotes[1][1].idempotency_key).not.toBe(quotes[0][1].idempotency_key);
    expect(screen.getByRole("checkbox", { name: /I confirm my billing country/ })).not.toBeChecked();
  });
  it("reads a captured original order after an interrupted request without a second checkout", async () => {
    state.post.mockImplementation((url: string) => url === "/v1/credits/orders" ? Promise.reject(new ApiError("Read original order", 409, { detail: { code: "billing_order_requires_status", order_id: "order_unified" } })) : post(url));
    state.get.mockImplementation((url: string) => url === "/v1/credits/orders/order_unified" ? Promise.resolve(order({ status: "paid", payment_status: "captured", fulfilled: true, checkout_available: false, paid_at: stamp, pass_expires_at: quote.pass_expires_at })) : read(url));
    const pay = await review(); await confirmIndia(); fireEvent.click(pay); await screen.findByRole("heading", { name: "Credit payment confirmed" }); noCheckout();
    expect(state.post.mock.calls.filter(([url]) => url === "/v1/credits/orders")).toHaveLength(1);
  });
  it("does not treat a provider browser callback as fulfillment authority", async () => {
    const pay = await review(); await confirmIndia(); fireEvent.click(pay); await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
    let finish!: (value: unknown) => void;
    state.get.mockImplementation((url: string) => url === "/v1/credits/orders/order_unified" ? new Promise((resolve) => { finish = resolve; }) : read(url));
    state.post.mockResolvedValue({ status: "received" });
    act(() => hostedCheckout.mock.calls[0][0].handler({ razorpay_payment_id: "fixture-payment", razorpay_order_id: "provider_unified", razorpay_signature: "fixture-signature" }));
    expect(screen.queryByRole("heading", { name: "Credit payment confirmed" })).not.toBeInTheDocument();
    await act(async () => { finish(order({ status: "paid", payment_status: "captured", fulfilled: true, checkout_available: false, paid_at: stamp, pass_expires_at: quote.pass_expires_at })); });
    expect(await screen.findByRole("heading", { name: "Credit payment confirmed" })).toBeInTheDocument();
  });
  it("keeps an unknown creation unpayable and reconciles before a separate explicit resume", async () => {
    const unknown = order({ provider_order_id: null, checkout_available: false, payment_status: "creation_unknown" });
    state.get.mockImplementation((url: string) => url === "/v1/credits/recent-order" ? Promise.resolve(unknown) : read(url));
    state.post.mockImplementation((url: string) => url.endsWith("/reconcile") ? Promise.resolve({ resolution: "checkout_ready", message: "Original payment ready" }) : post(url));
    render(<BillingPage />); const resume = await screen.findByRole("button", { name: "Resume original checkout" }); expect(resume).toBeDisabled(); expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeDisabled();
    await confirmIndia(); fireEvent.click(screen.getByRole("button", { name: "Reconcile original payment" })); await screen.findByText("Original payment ready");
    expect(screen.getByRole("checkbox", { name: /I confirm my billing country/ })).not.toBeChecked(); expect(resume).toBeDisabled(); noCheckout();
    await confirmIndia(); fireEvent.click(resume); await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
    expect(state.post.mock.calls.at(-1)).toEqual(["/v1/credits/orders", { quote_id: quote.quote_id, billing_country: "IN", idempotency_key: expect.any(String), order_reference: "order_unified" }]);
  });
  it("discloses the original pass services and minimum expiry before resumed payment after the catalog changes", async () => {
    const currentServices = [{ ...eligibleServices[0], operation: "interview_questions", credits: 5, deliverable: "Eight interview questions with guidance." }];
    state.get.mockImplementation((url: string) => url === "/v1/credits/recent-order" ? Promise.resolve(order())
      : url === "/v1/credits/catalog" ? Promise.resolve({ ...catalog, eligible_services: currentServices }) : read(url));
    render(<BillingPage />);
    const original = await screen.findByRole("region", { name: "Original credit pass terms" });
    expect(within(original).getByText("Resume tailoring · 8 credits per approved operation")).toBeInTheDocument();
    expect(within(original).queryByText(/Interview preparation/)).not.toBeInTheDocument();
    expect(original.querySelector(`time[datetime="${quote.pass_expires_at}"]`)).toBeInTheDocument();
    expect(original).toHaveTextContent("30-day pass");
    expect(original).toHaveTextContent("at least seven days after restoration");
    expect(within(original).getByRole("link", { name: "Review refund terms" })).toHaveAttribute("href", "/refund");
    expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled();
    await confirmIndia(); fireEvent.click(screen.getByRole("button", { name: "Resume original checkout" }));
    await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
    expect(state.post).toHaveBeenCalledExactlyOnceWith("/v1/credits/orders", { quote_id: quote.quote_id, billing_country: "IN", idempotency_key: expect.any(String), order_reference: "order_unified" });
  });
  it("keeps historical status readable but refuses to resume a pass whose original services are missing", async () => {
    state.get.mockImplementation((url: string) => url === "/v1/credits/recent-order" ? Promise.resolve(order({ accepted_quote: { ...quote, eligible_services: [] } })) : read(url));
    render(<BillingPage />); await screen.findByText(/The original supported services could not be verified/);
    await confirmIndia(); const resume = screen.getByRole("button", { name: "Resume original checkout" });
    expect(resume).toBeDisabled(); fireEvent.click(resume);
    expect(screen.getByRole("button", { name: "Check payment status" })).toBeEnabled();
    expect(state.post).not.toHaveBeenCalled(); noCheckout();
  });
  it("keeps unresolved reconciliation held even if a status response includes a provider ID", async () => {
    state.get.mockImplementation((url: string) => url === "/v1/credits/recent-order" ? Promise.resolve(order()) : read(url));
    state.post.mockResolvedValue({ resolution: "unresolved", message: "Original payment unresolved" });
    render(<BillingPage />); fireEvent.click(await screen.findByRole("button", { name: "Reconcile original payment" })); await screen.findByText("Original payment unresolved");
    await confirmIndia(); expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled(); noCheckout();
  });
});

describe("legacy payment recovery", () => {
  it("resumes only the original order's accepted terms and blocks a new unified purchase", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder()) : read(url)); state.post.mockResolvedValue(legacyCheckout);
    render(<BillingPage />); const resume = await screen.findByRole("button", { name: "Resume original checkout" }); expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeDisabled();
    expect(screen.getByLabelText("Original checkout terms")).toHaveTextContent("100 service credits + 2 analysis units for ₹649");
    await confirmIndia(); fireEvent.click(resume); await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
    expect(state.post.mock.calls).toEqual([["/billing/orders", { sku: "starter_bundle", billing_country: "IN", accepted_quote: legacyQuote, order_reference: "order_legacy", idempotency_key: expect.any(String) }]]);
    expect(hostedCheckout.mock.calls[0][0]).toMatchObject({ amount: 64900, order_id: "provider_legacy" });
  });
  it.each([{ accepted_quote: null }, { provider_order_id: null }, { checkout_available: false }])("cannot resume an unresolved or unreviewable original order %j", async (change) => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder(change)) : read(url));
    render(<BillingPage />); await screen.findByText("Order reference: order_legacy"); await confirmIndia();
    expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled(); expect(state.post).not.toHaveBeenCalled(); noCheckout();
  });
  it("blocks mismatched original order totals before loading the processor", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder()) : read(url)); state.post.mockResolvedValue({ ...legacyCheckout, amount_minor: 69900 });
    render(<BillingPage />); const resume = await screen.findByRole("button", { name: "Resume original checkout" }); await confirmIndia(); fireEvent.click(resume); await screen.findByRole("alert"); noCheckout();
  });
  it("recovers a captured legacy order during resume without opening another checkout", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder())
      : url === "/billing/orders/order_legacy" ? Promise.resolve(legacyOrder({ status: "paid", fulfilled: true, checkout_available: false })) : read(url));
    state.post.mockRejectedValue(new ApiError("Check original payment", 409, { detail: { code: "billing_order_requires_status", order_id: "order_legacy" } }));
    render(<BillingPage />); const resume = await screen.findByRole("button", { name: "Resume original checkout" }); await confirmIndia(); fireEvent.click(resume);
    await screen.findByRole("heading", { name: "Payment confirmed" }); expect(state.post).toHaveBeenCalledOnce(); noCheckout();
  });
  it("requires fresh acceptance after reconciliation restores original checkout", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder({ provider_order_id: null, checkout_available: false })) : read(url));
    state.post.mockResolvedValueOnce({ resolution: "checkout_ready", message: "Legacy payment ready", order: legacyOrder() }).mockResolvedValueOnce(legacyCheckout);
    render(<BillingPage />); const reconcile = await screen.findByRole("button", { name: "Reconcile original payment" }); await confirmIndia(); fireEvent.click(reconcile); await screen.findByText("Legacy payment ready");
    expect(screen.getByRole("checkbox", { name: /I confirm my billing country/ })).not.toBeChecked(); expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled(); noCheckout();
    await confirmIndia(); fireEvent.click(screen.getByRole("button", { name: "Resume original checkout" })); await waitFor(() => expect(openCheckout).toHaveBeenCalledOnce());
  });
  it("keeps unresolved legacy reconciliation blocked through status reads", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder()) : read(url));
    state.post.mockResolvedValue({ resolution: "unresolved", message: "Legacy payment unresolved", order: legacyOrder() });
    render(<BillingPage />); fireEvent.click(await screen.findByRole("button", { name: "Reconcile original payment" })); await screen.findByText("Legacy payment unresolved"); await confirmIndia();
    vi.useFakeTimers(); await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Check payment status" })); await vi.runAllTimersAsync(); }); vi.useRealTimers();
    expect(screen.getByRole("button", { name: "Resume original checkout" })).toBeDisabled(); expect(state.post).toHaveBeenCalledTimes(1); noCheckout();
  });
  it("confirms settled legacy payments through reconciliation without opening checkout", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder()) : read(url));
    state.post.mockResolvedValue({ resolution: "settled", message: "Original purchase delivered", order: legacyOrder({ status: "paid", fulfilled: true, checkout_available: false }) });
    render(<BillingPage />); fireEvent.click(await screen.findByRole("button", { name: "Reconcile original payment" })); await screen.findByRole("heading", { name: "Payment confirmed" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeEnabled()); noCheckout();
  });
  it("retains older premium purchase status without inventing credit conversion", async () => {
    state.get.mockImplementation((url: string) => url === "/billing/recent-order" ? Promise.resolve(legacyOrder({ sku: "premium_30d", status: "paid", fulfilled: true, accepted_quote: null, checkout_available: false })) : read(url));
    render(<BillingPage />); await screen.findByRole("heading", { name: "Payment confirmed" }); await waitFor(() => expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeEnabled()); noCheckout();
  });
});

it("refreshes only the pass eligibility after an explicitly confirmed participation choice", async () => {
  let optedIn = false;
  const invitation = { status: "NOT_OPTED_IN", review_id: "fixture-canary-v1", review_sha256: "a".repeat(64),
    notice_sha256: "b".repeat(64), runtime_sha256: "c".repeat(64), expires_at: "2030-10-29T00:00:00Z",
    notice: "Participation is voluntary. Review each price, keep control of applications and stop at any time.",
    can_opt_in: true, execution_flag_enabled: true, accepted_work_survives_revocation: true };
  state.get.mockImplementation((url: string) => url.startsWith("/v1/credits/canary-enrollment")
    ? Promise.resolve({ ...invitation, status: optedIn ? "OPTED_IN" : "NOT_OPTED_IN" })
    : url === "/v1/credits/catalog" ? Promise.resolve({ ...catalog, checkout_enabled: optedIn }) : read(url));
  state.post.mockImplementation((url: string) => {
    if (url === "/v1/credits/canary-enrollment") { optedIn = true; return Promise.resolve({ status: "CHOICE_RECORDED" }); }
    return post(url);
  });
  render(<BillingPage />);
  const pass = await screen.findByRole("button", { name: "Review Starter pass" });
  expect(pass).toBeDisabled();
  fireEvent.click(await screen.findByRole("checkbox", { name: /participation notice/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review participation" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm participation" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeEnabled());
  expect(state.get.mock.calls.filter(([url]) => url === "/v1/credits/catalog")).toHaveLength(2);
  expect(state.get.mock.calls.filter(([url]) => url === "/v1/credits/wallet")).toHaveLength(1);
  expect(state.get.mock.calls.filter(([url]) => url === "/v1/credits/history?limit=20")).toHaveLength(1);
  expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/credits/canary-enrollment"]);
  noCheckout();
});

it("does not let an earlier slow eligible catalog overwrite the refreshed stopped-participation state", async () => {
  let optedIn = true, catalogCalls = 0, finishOld!: (value: unknown) => void;
  const invitation = { status: "OPTED_IN", review_id: "fixture-canary-v1", review_sha256: "a".repeat(64),
    notice_sha256: "b".repeat(64), runtime_sha256: "c".repeat(64), expires_at: "2030-10-29T00:00:00Z",
    notice: "Participation is voluntary. Review each price, keep control of applications and stop at any time.",
    can_opt_in: false, execution_flag_enabled: false, accepted_work_survives_revocation: true };
  state.get.mockImplementation((url: string) => {
    if (url.startsWith("/v1/credits/canary-enrollment")) return Promise.resolve({ ...invitation, status: optedIn ? "OPTED_IN" : "NOT_OPTED_IN" });
    if (url === "/v1/credits/catalog") {
      catalogCalls += 1;
      return catalogCalls === 1 ? new Promise((resolve) => { finishOld = resolve; }) : Promise.resolve({ ...catalog, checkout_enabled: false });
    }
    return read(url);
  });
  state.post.mockImplementation((url: string) => {
    if (url === "/v1/credits/canary-enrollment") { optedIn = false; return Promise.resolve({ status: "CHOICE_RECORDED" }); }
    return post(url);
  });
  render(<BillingPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Stop participating" }));
  expect(await screen.findByRole("button", { name: "Review Starter pass" })).toBeDisabled();
  await act(async () => finishOld(catalog));
  expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeDisabled();
  expect(catalogCalls).toBe(2); noCheckout();
});
