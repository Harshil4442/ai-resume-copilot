const eligibleServices = [{ operation: "resume_tailor", credits: 8, deliverable: "Tailored resume with preserved source layout.", supported_limits: { max_pages: 2 }, source_formats: ["pdf", "docx"], route_version: "reviewed-v1" }];
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseUnifiedCreditCatalog, parseUnifiedPurchaseQuote } from "../../lib/unifiedCredits";
import { UnifiedCreditPacks, UnifiedPurchaseQuoteReview } from "./UnifiedCreditPacks";

const starts = "2026-10-01T00:00:00Z", ends = "2026-10-31T00:00:00Z";
const serverTime = "2026-10-30T23:59:59Z";
const campaign = { id: "launch", version: "1", starts_at: starts, ends_at: ends,
  timezone: "Asia/Kolkata", status: "active", standard_amount_minor: 7900, reference_price_verified: false };
const pack = { sku: "starter_pass", name: "Starter", amount_minor: 5900, credits: 40,
  duration_days: 30, auto_renews: false, purchase_enabled: true, terms_version: "terms-v1", campaign };
function catalog(changes: Record<string, unknown> = {}) {
  return parseUnifiedCreditCatalog({ schema_version: "unified-credits-v1", eligible_services: eligibleServices, catalog_version: "price-v1",
    server_time: serverTime, market: "IN", currency: "INR", checkout_enabled: true,
    products: [pack], ...changes })!;
}
function quote(changes: Record<string, unknown> = {}) {
  return parseUnifiedPurchaseQuote({ schema_version: "unified-credits-v1", quote_id: "quote-owned",
    sku: "starter_pass", name: "Starter", eligible_services: eligibleServices, catalog_version: "price-v1", terms_version: "terms-v1",
    amount_minor: 5900, currency: "INR", credits: 40, duration_days: 30, auto_renews: false,
    created_at: "2026-10-30T23:45:00Z", expires_at: ends, pass_expires_at: "2026-11-29T23:45:00Z",
    server_time: serverTime, campaign, ...changes })!;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("UnifiedCreditPacks", () => {
  it("shows one finite balance, genuine introductory terms and no invented savings", () => {
    const onReview = vi.fn();
    const { container } = render(<UnifiedCreditPacks catalog={catalog()} onReview={onReview} />);
    const card = screen.getByRole("article", { name: "Starter" });
    expect(within(card).getByText("Introductory price")).toBeInTheDocument();
    expect(within(card).getByText("₹59")).toBeInTheDocument();
    expect(within(card).getByText("40 credits")).toBeInTheDocument();
    expect(within(card).getByText(/30 days from verified payment/)).toBeInTheDocument();
    expect(card.querySelector(`time[datetime="${ends}"]`)).toBeInTheDocument();
    expect(container.querySelector("s")).toBeNull();
    expect(screen.queryByText(/Save ₹/)).not.toBeInTheDocument();
    expect(screen.queryByText("₹79")).not.toBeInTheDocument();
    expect(screen.getByText(/No subscription or automatic renewal/)).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Review Starter pass" }));
    expect(onReview).toHaveBeenCalledExactlyOnceWith("starter_pass");
  });

  it("shows a reference price and exact savings only when the active campaign is verified", () => {
    const data = catalog({ products: [{ ...pack, campaign: { ...campaign, reference_price_verified: true } }] });
    const { container } = render(<UnifiedCreditPacks catalog={data} onReview={vi.fn()} />);
    expect(screen.getByText("Limited offer")).toBeInTheDocument();
    expect(container.querySelector("s")).toHaveTextContent("₹79");
    expect(screen.getByText(/Save ₹20/)).toBeInTheDocument();
  });

  it("removes offer claims and disables stale prices when the real deadline passes", async () => {
    const refresh = vi.fn(), review = vi.fn();
    const data = catalog({ products: [{ ...pack, campaign: { ...campaign, reference_price_verified: true } }] });
    const { container } = render(<UnifiedCreditPacks catalog={data} onReview={review} onRefresh={refresh} />);
    await act(async () => { vi.advanceTimersByTime(1_000); });
    expect(screen.queryByText("Limited offer")).not.toBeInTheDocument();
    expect(container.querySelector("s")).toBeNull();
    const reviewButton = screen.getByRole("button", { name: "Review Starter pass" });
    expect(reviewButton).toBeDisabled();
    fireEvent.click(reviewButton);
    expect(review).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh prices" }));
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.queryByText("₹79")).not.toBeInTheDocument();
  });

  it("requires a new catalog at campaign start instead of inventing a discounted price", async () => {
    const data = catalog({ server_time: "2026-09-30T23:59:59Z", products: [{ ...pack, amount_minor: 7900,
      campaign: { ...campaign, status: "scheduled" } }] });
    render(<UnifiedCreditPacks catalog={data} onReview={vi.fn()} />);
    expect(screen.queryByText("Introductory price")).not.toBeInTheDocument();
    expect(screen.getByText("₹79")).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(1_000); });
    expect(screen.getByRole("button", { name: "Review Starter pass" })).toBeDisabled();
    expect(screen.queryByText("₹59")).not.toBeInTheDocument();
  });

  it("keeps a disabled checkout unavailable and names the missing capability", () => {
    const review = vi.fn();
    render(<UnifiedCreditPacks catalog={catalog({ checkout_enabled: false })} onReview={review} />);
    const button = screen.getByRole("button", { name: "Review Starter pass" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(review).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Credit purchases are not available yet");
  });

  it("prevents duplicate quote requests while a review is preparing", () => {
    render(<UnifiedCreditPacks catalog={catalog()} onReview={vi.fn()} reviewingSku="starter_pass" />);
    expect(screen.getByRole("button", { name: "Preparing quote…" })).toBeDisabled();
  });

  it("shows no invented passes when the server returns an empty catalogue", () => {
    render(<UnifiedCreditPacks catalog={catalog({ products: [] })} onReview={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("No credit pass is available");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("UnifiedPurchaseQuoteReview", () => {
  it("shows exact terms and accepts only the reviewed server quote ID", () => {
    const accept = vi.fn();
    render(<UnifiedPurchaseQuoteReview quote={quote()} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled />);
    expect(screen.getByText("₹59")).toBeInTheDocument();
    expect(screen.getByText(/40 credits · 30 days · no automatic renewal/)).toBeInTheDocument();
    expect(screen.getByText("Minimum guaranteed credit expiry")).toBeInTheDocument();
    expect(screen.getByText(/at least seven days after restoration/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue with ₹59 payment" }));
    expect(accept).toHaveBeenCalledExactlyOnceWith("quote-owned");
  });

  it("expires at the server-anchored deadline and requires another explicit review", async () => {
    const accept = vi.fn(), refresh = vi.fn();
    render(<UnifiedPurchaseQuoteReview quote={quote()} onAccept={accept} onRefresh={refresh} checkoutEnabled />);
    await act(async () => { vi.advanceTimersByTime(1_000); });
    const pay = screen.getByRole("button", { name: "Continue with ₹59 payment" });
    expect(pay).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("This quote expired");
    fireEvent.click(pay); expect(accept).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Get a fresh quote" }));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("retains an unexpired quote after its original campaign has ended", () => {
    const data = quote({ created_at: "2026-10-30T23:59:00Z", expires_at: "2026-10-31T00:14:00Z",
      pass_expires_at: "2026-11-29T23:59:00Z", server_time: "2026-10-31T00:05:00Z" });
    render(<UnifiedPurchaseQuoteReview quote={data} onAccept={vi.fn()} onRefresh={vi.fn()} checkoutEnabled />);
    expect(screen.getByRole("button", { name: "Continue with ₹59 payment" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reviews a fresh server quote after expiry without inheriting the old timer", async () => {
    const accept = vi.fn();
    const view = render(<UnifiedPurchaseQuoteReview quote={quote()} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled />);
    await act(async () => { vi.advanceTimersByTime(1_000); });
    expect(screen.getByRole("button", { name: "Continue with ₹59 payment" })).toBeDisabled();
    const fresh = quote({ quote_id: "fresh-owned", amount_minor: 7900, campaign: null,
      created_at: "2026-10-31T00:01:00Z", server_time: "2026-10-31T00:01:00Z",
      expires_at: "2026-10-31T00:16:00Z", pass_expires_at: "2026-11-30T00:01:00Z" });
    view.rerender(<UnifiedPurchaseQuoteReview quote={fresh} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue with ₹79 payment" }));
    expect(accept).toHaveBeenCalledExactlyOnceWith("fresh-owned");
  });

  it("does not extend a quote when the device wall clock is moved back", async () => {
    render(<UnifiedPurchaseQuoteReview quote={quote()} onAccept={vi.fn()} onRefresh={vi.fn()} checkoutEnabled />);
    vi.setSystemTime(new Date("2020-01-01T00:00:00Z"));
    await act(async () => { vi.advanceTimersByTime(1_000); });
    expect(screen.getByRole("button", { name: "Continue with ₹59 payment" })).toBeDisabled();
  });

  it("does not begin payment when checkout is held or the caller is busy", () => {
    const accept = vi.fn();
    const view = render(<UnifiedPurchaseQuoteReview quote={quote()} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled={false} />);
    expect(screen.getByRole("button", { name: "Continue with ₹59 payment" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Checkout is currently unavailable");
    view.rerender(<UnifiedPurchaseQuoteReview quote={quote()} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled busy />);
    expect(screen.getByRole("button", { name: "Opening secure checkout…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Get a fresh quote" })).toBeDisabled();
    expect(accept).not.toHaveBeenCalled();
  });
});


it("discloses supported services, exact charges and source limits before purchase approval", () => {
  const value = quote(); const accept = vi.fn();
  const view = render(<UnifiedPurchaseQuoteReview quote={value} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled />);
  expect(screen.getByText(/Resume tailoring · 8 credits per approved operation/)).toBeVisible();
  expect(screen.getByText("Maximum pages: 2")).toBeVisible();
  expect(screen.getByText("Supported source formats: PDF, DOCX")).toBeVisible();
  view.rerender(<UnifiedPurchaseQuoteReview quote={{ ...value, eligible_services: [] }} onAccept={accept} onRefresh={vi.fn()} checkoutEnabled />);
  expect(screen.getByRole("button", { name: /Continue with/ })).toBeDisabled(); expect(accept).not.toHaveBeenCalled();
});
