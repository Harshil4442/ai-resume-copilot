"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ApiError, apiGet, apiPostJson } from "../../lib/api";
import { boundedStatusChecks, newStatusReadBudget, type StatusReadBudget } from "../../lib/boundedStatusPolling";
import {
  formatCreditDate, formatCreditPrice, parseUnifiedCheckoutOrder, parseUnifiedCreditCatalog,
  parseUnifiedCreditHistory, parseUnifiedCreditOrder, parseUnifiedPurchaseQuote,
  sameUnifiedPurchaseQuote, type UnifiedCreditCatalog, type UnifiedCreditHistory, type UnifiedCreditOrder,
  type UnifiedCreditWallet, type UnifiedPassSku, type UnifiedPurchaseQuote,
} from "../../lib/unifiedCredits";
import { Button } from "../ui/Button";
import { LoadingBlock } from "../ui/LoadingBlock";
import { EligibleCreditServices, UnifiedCreditPacks, UnifiedPurchaseQuoteReview } from "./UnifiedCreditPacks";
import { loadHostedCheckout } from "./hostedCheckout";
import { fetchOwnedWallet } from "../../lib/ownedBootstrapClient";

const messageOf = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const unsettled = (order: UnifiedCreditOrder | null) => order?.status === "pending" || (order?.status === "paid" && !order.fulfilled);

export function UnifiedBillingPanel({ legacyPending, requestedSku, participationRevision = 0 }: { legacyPending: boolean; requestedSku: string | null; participationRevision?: number }) {
  const client = useQueryClient();
  const statusWork = useRef<AbortController | null>(null);
  const statusBudgets = useRef(new Map<string, StatusReadBudget>());
  const mounted = useRef(true), actionPending = useRef(false), pollId = useRef(0);
  const catalogRead = useRef(0);
  const quoteIntent = useRef<{ sku: UnifiedPassSku; key: string } | null>(null);
  const orderIntent = useRef<{ quoteId: string; key: string } | null>(null);
  const [catalog, setCatalog] = useState<UnifiedCreditCatalog | null>(null), [catalogLoading, setCatalogLoading] = useState(true);
  const [wallet, setWallet] = useState<UnifiedCreditWallet | null>(null), [walletLoading, setWalletLoading] = useState(true);
  const [history, setHistory] = useState<UnifiedCreditHistory | null>(null), [historyBusy, setHistoryBusy] = useState(true);
  const [quote, setQuote] = useState<UnifiedPurchaseQuote | null>(null), [reviewingSku, setReviewingSku] = useState<UnifiedPassSku | null>(null);
  const [order, setOrder] = useState<UnifiedCreditOrder | null>(null), [recentRead, setRecentRead] = useState<"loading" | "ready" | "failed">("loading");
  const [unknownAttempt, setUnknownAttempt] = useState(false), [knownOrderId, setKnownOrderId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [indiaConfirmed, setIndiaConfirmed] = useState(false), [reconciliationHeld, setReconciliationHeld] = useState(false);
  const [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  const quoteSection = useRef<HTMLDivElement>(null);

  const refreshCatalog = useCallback(() => {
    const read = ++catalogRead.current;
    return apiGet<unknown>("/v1/credits/catalog").then((raw) => {
    const value = parseUnifiedCreditCatalog(raw);
    if (!value) throw new Error("Current pass prices could not be verified.");
    if (mounted.current && read === catalogRead.current) setCatalog(value);
  }).catch(() => { if (mounted.current && read === catalogRead.current) setCatalog(null); })
    .finally(() => { if (mounted.current && read === catalogRead.current) setCatalogLoading(false); });
  }, []);
  const refreshWallet = useCallback((force = false) => fetchOwnedWallet(client, force).then((value) => {
    if (mounted.current) setWallet(value);
  }).catch(() => { if (mounted.current) setWallet(null); })
    .finally(() => { if (mounted.current) setWalletLoading(false); }), [client]);
  const refreshHistory = useCallback((cursor?: string) => apiGet<unknown>(`/v1/credits/history?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`).then((raw) => {
    const value = parseUnifiedCreditHistory(raw);
    if (!value) throw new Error("Credit history could not be verified.");
    if (mounted.current) setHistory((old) => cursor && old ? { ...value, items: [...old.items, ...value.items.filter((item) => !old.items.some((previous) => previous.id === item.id))] } : value);
  }).catch(() => { if (mounted.current) setError("Credit history could not be loaded. Your balance has not been changed."); })
    .finally(() => { if (mounted.current) setHistoryBusy(false); }), []);
  const acceptStatus = useCallback(async (value: UnifiedCreditOrder) => {
    if (!mounted.current) return;
    setOrder(value); setKnownOrderId(value.order_id); setUnknownAttempt(false);
    if (value.status === "paid" && value.fulfilled) {
      setError(null); setMessage("Payment confirmed. Your purchased credits have been delivered."); setReconciliationHeld(false);
      setQuote(null); quoteIntent.current = null; orderIntent.current = null;
      window.dispatchEvent(new Event("refresh_unified_credits"));
      await Promise.allSettled([refreshWallet(true), refreshHistory()]);
    } else if (value.status === "refunded") {
      setQuote(null); quoteIntent.current = null; orderIntent.current = null; setIndiaConfirmed(false);
      setMessage("This payment was refunded. Review the refund and balance in your history."); setReconciliationHeld(false);
      await Promise.allSettled([refreshWallet(true), refreshHistory()]);
    } else if (value.status === "failed") {
      setQuote(null); quoteIntent.current = null; orderIntent.current = null; setIndiaConfirmed(false);
      setMessage("This payment was not completed. Review a fresh quote before a new purchase."); setReconciliationHeld(false);
    }
  }, [refreshHistory, refreshWallet]);
  const readOrder = useCallback(async (identity: string, signal?: AbortSignal) => {
    const value = parseUnifiedCreditOrder(await apiGet<unknown>(`/v1/credits/orders/${encodeURIComponent(identity)}`, signal));
    signal?.throwIfAborted();
    if (!mounted.current) throw new DOMException("Status check stopped", "AbortError");
    if (!value || value.order_id !== identity) throw new Error("The original payment status could not be verified.");
    await acceptStatus(value); return value;
  }, [acceptStatus]);
  const readRecent = useCallback(() => apiGet<unknown>("/v1/credits/recent-order").then(async (result) => {
    const value = result === null ? null : parseUnifiedCreditOrder(result);
    if (result !== null && !value) throw new Error("Payment status could not be verified.");
    if (!mounted.current) return;
    if (value) await acceptStatus(value);
    if (mounted.current) setRecentRead("ready");
  }).catch(() => { if (mounted.current) setRecentRead("failed"); }), [acceptStatus]);

  useEffect(() => {
    mounted.current = true;
    void Promise.allSettled([refreshWallet(), refreshHistory(), readRecent()]);
    return () => { mounted.current = false; pollId.current += 1; statusWork.current?.abort(); };
  }, [readRecent, refreshHistory, refreshWallet]);
  useEffect(() => { void refreshCatalog(); }, [refreshCatalog, participationRevision]);
  useEffect(() => { if (quote) quoteSection.current?.focus(); }, [quote]);

  const purchaseHeld = legacyPending || recentRead !== "ready" || unsettled(order) || unknownAttempt;
  async function prepareQuote(sku: UnifiedPassSku, fresh = false) {
    if (purchaseHeld || actionPending.current || busy || catalog?.checkout_enabled !== true
      || !catalog.products.some((product) => product.sku === sku && product.purchase_enabled)) return;
    actionPending.current = true; setReviewingSku(sku); setError(null); setIndiaConfirmed(false);
    if (fresh || quoteIntent.current?.sku !== sku) quoteIntent.current = { sku, key: crypto.randomUUID() };
    try {
      const value = parseUnifiedPurchaseQuote(await apiPostJson<unknown>("/v1/credits/purchase-quotes", { sku, idempotency_key: quoteIntent.current.key }));
      if (!value || value.sku !== sku) throw new Error("The purchase quote could not be verified. No payment has started.");
      if (mounted.current) { setQuote(value); setMessage(null); }
    } catch (failure) { if (mounted.current) setError(messageOf(failure, "Could not prepare a purchase quote.")); }
    finally { actionPending.current = false; if (mounted.current) setReviewingSku(null); }
  }
  const checkStatus = useCallback(async (identity: string, automatic = false) => {
    if (!mounted.current) return;
    statusWork.current?.abort();
    const work = new AbortController(); statusWork.current = work;
    const run = ++pollId.current;
    const budget = statusBudgets.current.get(identity) ?? newStatusReadBudget();
    statusBudgets.current.set(identity, budget);
    setBusy(true); setError(null);
    try {
      const outcome = await boundedStatusChecks({ budget, signal: work.signal, automatic,
        read: (signal) => readOrder(identity, signal), active: (value) => Boolean(unsettled(value)) });
      if (mounted.current && run === pollId.current && outcome !== "settled") setMessage("Payment confirmation is pending. Automatic checks are paused; use Check payment status for one fresh read. If you were charged, do not pay again.");
    } catch (failure) { if (mounted.current && run === pollId.current && !work.signal.aborted) setError(messageOf(failure, "Could not verify payment.")); }
    finally { if (mounted.current && run === pollId.current) setBusy(false); }
  }, [readOrder]);

  async function startPayment(accepted: UnifiedPurchaseQuote, resumeOrder?: UnifiedCreditOrder) {
    if (actionPending.current || busy || legacyPending || recentRead !== "ready" || catalog?.checkout_enabled !== true
      || !indiaConfirmed || accepted.eligible_services.length === 0
      || (resumeOrder ? !resumeOrder.checkout_available || reconciliationHeld : unsettled(order))) return;
    actionPending.current = true; setBusy(true); setError(null);
    if (orderIntent.current?.quoteId !== accepted.quote_id) orderIntent.current = { quoteId: accepted.quote_id, key: crypto.randomUUID() };
    try {
      const raw = await apiPostJson<unknown>("/v1/credits/orders", { quote_id: accepted.quote_id, billing_country: "IN",
        idempotency_key: orderIntent.current.key, ...(resumeOrder ? { order_reference: resumeOrder.order_id } : {}) });
      const checkout = parseUnifiedCheckoutOrder(raw, accepted);
      if (!checkout || (resumeOrder && (checkout.order_id !== resumeOrder.order_id || checkout.provider_order_id !== resumeOrder.provider_order_id))) throw new Error("Checkout terms could not be verified. Check the original payment before another attempt.");
      if (!mounted.current) return;
      setKnownOrderId(checkout.order_id);
      const current = await readOrder(checkout.order_id);
      if (!sameUnifiedPurchaseQuote(current.accepted_quote, accepted)) throw new Error("The original order terms changed. Checkout remains paused.");
      if (current.status === "paid" && current.fulfilled) { setBusy(false); return; }
      if (!current.checkout_available || current.provider_order_id !== checkout.provider_order_id) throw new Error("The original payment needs reconciliation before checkout can resume.");
      await loadHostedCheckout();
      if (!mounted.current || !window.Razorpay) return;
      let callbackReceived = false;
      const hosted = new window.Razorpay({ key: checkout.key_id, amount: checkout.amount_minor, currency: checkout.currency,
        name: checkout.name, description: checkout.description, order_id: checkout.provider_order_id, prefill: checkout.prefill ?? undefined,
        handler: (callback) => {
          if (callbackReceived || !mounted.current) return;
          callbackReceived = true;
          void apiPostJson(`/billing/orders/${encodeURIComponent(checkout.order_id)}/checkout-result`, callback).catch(() => {});
          void checkStatus(checkout.order_id, true);
        }, modal: { confirm_close: true, ondismiss: () => { if (!callbackReceived && mounted.current) {
          setBusy(false); setMessage("Checkout closed. No payment is assumed. Check the original payment before trying again.");
        } } }, retry: { enabled: true }, theme: { color: "#485c11" },
      });
      hosted.on("payment.failed", () => { if (mounted.current) { setBusy(false); setMessage("The payment attempt was not completed. Check its status before retrying."); } });
      hosted.open();
    } catch (failure) {
      if (!mounted.current) return;
      const detail = failure instanceof ApiError ? (failure.detail as { detail?: { code?: string; order_id?: string; order_created?: boolean } })?.detail : null;
      if (detail?.order_created === false && ["credit_quote_rejected", "credit_release_unavailable"].includes(detail.code ?? "")) {
        setUnknownAttempt(false); setQuote(null); quoteIntent.current = null; orderIntent.current = null;
        setIndiaConfirmed(false); setBusy(false);
        setError(messageOf(failure, "No order was created. Review the current pass terms before trying again."));
        void refreshCatalog(); return;
      }
      if (detail?.code === "billing_order_requires_status" && typeof detail.order_id === "string") {
        setKnownOrderId(detail.order_id);
        try {
          const original = await readOrder(detail.order_id);
          if (original.status === "paid" && original.fulfilled) { setBusy(false); return; }
        } catch { setUnknownAttempt(true); }
      } else setUnknownAttempt(true);
      setError(messageOf(failure, "The payment request was interrupted. Recover the same request or check status before paying again.")); setBusy(false);
    } finally { actionPending.current = false; }
  }
  async function reconcile() {
    if (!order || !unsettled(order) || actionPending.current || busy) return;
    actionPending.current = true; setBusy(true); setIndiaConfirmed(false); setReconciliationHeld(true); setError(null);
    try {
      const result = await apiPostJson<{ resolution: string; message: string }>(`/billing/orders/${encodeURIComponent(order.order_id)}/reconcile`, undefined);
      if (!["unresolved", "checkout_ready", "settled"].includes(result.resolution)) throw new Error("Payment reconciliation could not be verified.");
      const refreshed = await readOrder(order.order_id);
      if (!sameUnifiedPurchaseQuote(refreshed.accepted_quote, order.accepted_quote)) throw new Error("The original terms need billing support review.");
      if (mounted.current) { setReconciliationHeld(result.resolution === "unresolved"); setMessage(result.message); }
    } catch (failure) { if (mounted.current) setError(messageOf(failure, "Could not reconcile the original payment.")); }
    finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }

  return <section className="space-y-8" aria-label="Credit wallet and purchases">
    <section className="surface-panel p-5 sm:p-6" aria-labelledby="credit-wallet-heading">
      <h2 id="credit-wallet-heading" className="font-display text-2xl">Your credit wallet</h2>
      {walletLoading ? <LoadingBlock rows={2} /> : wallet ? <>
        <div className="mt-5 grid gap-5 sm:grid-cols-3"><div><p className="data-label">Available credits</p><p className="mt-2 text-3xl font-semibold">{wallet.available_credits}</p></div><div><p className="data-label">Reserved credits</p><p className="mt-2 text-2xl">{wallet.reserved_credits}</p></div><div><p className="data-label">Pending balance adjustment</p><p className="mt-2 text-2xl">{wallet.debt_credits}</p></div></div>
        {wallet.lots.length ? <details className="mt-5"><summary className="cursor-pointer text-sm font-semibold">Credit expiry details</summary><ul className="mt-3 space-y-2 text-sm">{wallet.lots.map((lot) => <li key={lot.grant_id}>{lot.remaining_credits} credits · {lot.expires_at ? <>expires <time dateTime={lot.expires_at}>{formatCreditDate(lot.expires_at)}</time> (Asia/Kolkata)</> : "original no-expiry terms"}</li>)}</ul></details> : <p className="mt-4 text-sm text-muted-foreground">No credit grants yet.</p>}
        <p className="mt-4 text-xs leading-6 text-muted-foreground">Available balance already accounts for reservations and adjustments. Credit grants are used in their eligible expiry order. Older entitlements below are not added to this wallet.</p>
      </> : <p className="mt-4 text-sm">Your credit balance is unavailable. No zero balance has been assumed. <button type="button" className="underline" onClick={() => void refreshWallet()}>Refresh balance</button></p>}
    </section>
    {error ? <p role="alert" className="rounded-xl border border-coral/30 bg-coral/5 p-4 text-sm text-coral">{error}</p> : null}
    {message ? <p role="status" className="rounded-xl border border-border p-4 text-sm leading-6">{message}</p> : null}
    {legacyPending ? <p role="status" className="text-sm">Previous purchases are being checked or need attention. New purchases stay paused until the original payment is resolved.</p> : null}
    {recentRead === "failed" ? <div role="status" className="text-sm"><p>Could not verify your recent credit purchase. No new payment can start.</p><Button variant="secondary" className="mt-3" onClick={() => { setRecentRead("loading"); void readRecent(); }}>Check recent purchase</Button></div> : null}
    {catalogLoading ? <LoadingBlock rows={4} /> : catalog ? <UnifiedCreditPacks catalog={catalog} onReview={(sku) => void prepareQuote(sku)} onRefresh={() => { setCatalogLoading(true); void refreshCatalog(); }} reviewingSku={reviewingSku} disabled={busy || purchaseHeld} /> : <div role="status" className="surface-panel p-5"><h2 className="font-display text-2xl">Credit passes are currently unavailable</h2><p className="mt-2 text-sm leading-6">Current prices could not be verified. Existing balances and previous payment recovery remain available.</p><Button variant="secondary" className="mt-4" onClick={() => { setCatalogLoading(true); void refreshCatalog(); }}>Refresh prices</Button></div>}
    {requestedSku && catalog?.products.some((product) => product.sku === requestedSku) && !quote ? <p className="text-sm text-muted-foreground">Review your selected {catalog.products.find((product) => product.sku === requestedSku)?.name} pass above to get its current quote.</p> : null}
    {(quote || order?.status === "pending") ? <label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" checked={indiaConfirmed} disabled={busy} onChange={(event) => setIndiaConfirmed(event.target.checked)} className="mt-1 h-4 w-4 accent-primary" /><span>I confirm my billing country is India and this INR purchase is for domestic use.</span></label> : null}
    {quote && !unsettled(order) && !unknownAttempt ? <div ref={quoteSection} tabIndex={-1} className="outline-none"><UnifiedPurchaseQuoteReview quote={quote} checkoutEnabled={catalog?.checkout_enabled === true}
      disabled={legacyPending || recentRead !== "ready" || !indiaConfirmed} disabledReason={!indiaConfirmed ? "Confirm your billing country above before continuing." : undefined}
      onAccept={(identity) => { if (identity === quote.quote_id) void startPayment(quote); }} busy={busy} onRefresh={() => void prepareQuote(quote.sku, true)} /></div> : null}
    {unknownAttempt ? <div role="status" className="surface-panel space-y-3 p-5"><p className="text-sm leading-6">The last payment request has an unknown outcome. Do not start a different purchase. Recovering uses the same request identity and original terms.</p>
      {knownOrderId ? <Button variant="secondary" disabled={busy} onClick={() => void checkStatus(knownOrderId)}>Check payment status</Button> : quote ? <Button variant="secondary" disabled={busy || !indiaConfirmed || legacyPending || catalog?.checkout_enabled !== true} onClick={() => void startPayment(quote)}>Recover interrupted checkout</Button> : null}</div> : null}
    {order ? <section className="surface-panel space-y-4 p-5" aria-labelledby="credit-order-heading">
      <h2 id="credit-order-heading" className="font-display text-2xl">{order.status === "paid" && order.fulfilled ? "Credit payment confirmed" : "Your credit purchase"}</h2>
      <p className="text-sm">{order.accepted_quote.credits} credits for {formatCreditPrice(order.amount_minor)} · no automatic renewal</p><p className="break-all text-xs">Order reference: {order.order_id}</p>
      {order.pass_expires_at ? <p className="text-sm">Credits expire <time dateTime={order.pass_expires_at}>{formatCreditDate(order.pass_expires_at)}</time> (Asia/Kolkata).</p> : null}
      {order.refunded_amount_minor > 0 ? <p className="text-sm">Refunded: {formatCreditPrice(order.refunded_amount_minor)}</p> : null}
      <section aria-label="Original credit pass terms" className="space-y-4 border-t border-border pt-4">
        <p className="text-sm leading-6">This order keeps its original {order.accepted_quote.duration_days}-day pass and supported services below, even if today’s offers change. Its minimum guaranteed credit expiry is <time dateTime={order.accepted_quote.pass_expires_at}>{formatCreditDate(order.accepted_quote.pass_expires_at)}</time> (Asia/Kolkata). Verified payment provides at least 30 full days; a later confirmation can extend this minimum.</p>
        <EligibleCreditServices services={order.accepted_quote.eligible_services} />
        <p className="break-words text-xs leading-6 text-muted-foreground">Accepted terms version: {order.accepted_quote.terms_version}. Failed or incomplete services restore credits usable for at least seven days after restoration, or receive the applicable monetary remedy. <Link href="/refund" prefetch={false} className="underline">Review refund terms</Link>.</p>
        {!order.accepted_quote.eligible_services.length && unsettled(order) ? <p role="status" className="text-sm">The original supported services could not be verified. Contact billing support before resuming this payment.</p> : null}
      </section>
      {unsettled(order) ? <><p className="text-sm leading-6">This original payment is not complete. Its accepted price and credit quantity remain unchanged. A browser callback does not confirm delivery.</p><div className="flex flex-wrap gap-3"><Button variant="secondary" disabled={busy} onClick={() => void checkStatus(order.order_id)}>Check payment status</Button><Button variant="secondary" disabled={busy} onClick={() => void reconcile()}>Reconcile original payment</Button><Button disabled={busy || reconciliationHeld || legacyPending || !indiaConfirmed || !order.checkout_available || order.accepted_quote.eligible_services.length === 0 || catalog?.checkout_enabled !== true} onClick={() => void startPayment(order.accepted_quote, order)}>Resume original checkout</Button></div></> : null}
    </section> : null}
    <section aria-labelledby="credit-history-heading"><h2 id="credit-history-heading" className="font-display text-2xl">Credit history</h2>
      {history ? history.items.length ? <ul className="mt-4 divide-y divide-border">{history.items.map((event) => <li key={event.id} className="grid gap-2 py-4 text-sm sm:grid-cols-[1fr_auto_auto]"><span>{event.event_type.replaceAll("_", " ")} · {formatCreditDate(event.created_at)}</span><span>{event.amount > 0 ? "+" : ""}{event.amount} credits</span><span>Balance {event.balance_after}</span></li>)}</ul> : <p className="mt-3 text-sm text-muted-foreground">No credit activity yet.</p> : <p className="mt-3 text-sm text-muted-foreground">{historyBusy ? "Loading credit history…" : "Credit history is unavailable."}</p>}
      {history?.next_cursor ? <Button variant="secondary" className="mt-4" disabled={historyBusy} onClick={() => { setHistoryBusy(true); void refreshHistory(history.next_cursor!); }}>Load older activity</Button> : null}
    </section>
    <p className="text-xs leading-5 text-muted-foreground">Payment confirmation updates while this tab is visible. If checks pause, use Check payment status before trying another payment.</p>
    <p className="text-xs leading-6 text-muted-foreground">Seller: HireWiz, operated by SAVALIYA HARSHIL YOGESHBHAI. Payment credentials are entered only in Razorpay hosted checkout. <Link href="/contact" className="underline">Billing support</Link> · <Link href="/digital-delivery" className="underline">Digital delivery</Link>.</p>
  </section>;
}
