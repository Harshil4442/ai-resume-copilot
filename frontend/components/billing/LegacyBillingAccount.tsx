"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ApiError, apiGet, apiPostJson } from "../../lib/api";
import { boundedStatusChecks, newStatusReadBudget, type StatusReadBudget } from "../../lib/boundedStatusPolling";
import { fetchOwnedProfile } from "../../lib/ownedBootstrapClient";
import { isFiniteBundleSku } from "../../lib/billingProducts";
import { formatCreditPrice, isUnifiedPassSku } from "../../lib/unifiedCredits";
import { Button } from "../ui/Button";
import { ServiceCreditHistory } from "./ServiceCreditHistory";
import { HostedCheckoutLoadError, loadHostedCheckout } from "./hostedCheckout";

type LegacyQuote = { catalog_version: string; amount_minor: number; currency: "INR"; job_service_credits: number; analysis_units: number };
type LegacyOrder = {
  order_id: string; provider_order_id?: string | null; sku: string; status: "pending" | "paid" | "failed" | "refunded";
  fulfilled: boolean; amount_minor: number; currency: "INR"; accepted_quote?: LegacyQuote | null;
  checkout_available?: boolean; payment_reference?: string | null; refunded_amount_minor?: number;
};
type LegacyCheckout = {
  order_id: string; provider: string; provider_order_id: string; key_id: string; amount_minor: number; currency: string;
  name: string; description: string; accepted_quote?: LegacyQuote; prefill?: { email?: string };
};
type UsageHistory = { balance: number; items: { id: string; event_type: string; amount: number; balance_after: number; created_at: string }[] };
function sameQuote(actual: LegacyQuote | null | undefined, expected: LegacyQuote) {
  return actual?.catalog_version === expected.catalog_version && actual.amount_minor === expected.amount_minor
    && actual.currency === expected.currency && actual.job_service_credits === expected.job_service_credits
    && actual.analysis_units === expected.analysis_units;
}
function signedBalance(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
}
function refundAmount(order: LegacyOrder): number | null {
  const refunded = signedBalance(order.refunded_amount_minor);
  const amount = signedBalance(order.amount_minor);
  if (order.currency !== "INR" || refunded === null || refunded < 0 || amount === null || amount <= 0 || refunded > amount) return null;
  if (order.status === "refunded") return refunded > 0 && refunded === amount ? refunded : null;
  if (order.status === "paid" && refunded < amount) return refunded;
  return null;
}
function errorText(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback; }
function canReconcile(order: LegacyOrder) {
  return order.status === "pending" || (order.status === "failed" && Boolean(order.provider_order_id && order.payment_reference));
}
function blocksNewPurchase(order: LegacyOrder) {
  return canReconcile(order) || (order.status === "paid" && !order.fulfilled);
}

/** Existing purchases remain usable on their original terms; this component never sells a new legacy pack. */
export function LegacyBillingAccount({ onPendingChange }: { onPendingChange: (pending: boolean) => void }) {
  const client = useQueryClient();
  const statusWork = useRef<AbortController | null>(null);
  const statusBudgets = useRef(new Map<string, StatusReadBudget>());
  const mounted = useRef(true), pollId = useRef(0), actionPending = useRef(false);
  const recentReadId = useRef(0);
  const balanceRead = useRef(0);
  const acceptedOrder = useRef<LegacyOrder | null>(null);
  const intent = useRef<{ orderId: string; key: string } | null>(null);
  const [profile, setProfile] = useState<{ tier?: string; ai_credits?: number; premium_until?: string | null } | null>(null);
  const [serviceCredits, setServiceCredits] = useState<number | null | undefined>(undefined);
  const [usage, setUsage] = useState<UsageHistory | null>(null);
  const [order, setOrder] = useState<LegacyOrder | null>(null);
  const [recentRead, setRecentRead] = useState<"loading" | "ready" | "failed">("loading");
  const [busy, setBusy] = useState(false), [indiaConfirmed, setIndiaConfirmed] = useState(false);
  const [reconciliationHeld, setReconciliationHeld] = useState(false);
  const [checkoutStatusRequired, setCheckoutStatusRequired] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  const pending = recentRead !== "ready" || (order !== null && blocksNewPurchase(order));
  useEffect(() => { onPendingChange(pending); }, [pending, onPendingChange]);

  const refreshBalances = useCallback(async (force = false) => {
    const read = ++balanceRead.current;
    const isCurrent = () => mounted.current && balanceRead.current === read;
    await Promise.allSettled([
      fetchOwnedProfile(client, force).then((value) => {
        if (isCurrent()) setProfile(value);
      }),
      apiGet<{ job_service_credits?: unknown }>("/auth/me").then((value) => {
        if (isCurrent()) setServiceCredits(signedBalance(value.job_service_credits));
      }).catch(() => { if (isCurrent()) setServiceCredits(null); }),
      apiGet<UsageHistory>("/v1/usage-events?limit=20").then((value) => { if (isCurrent()) setUsage(value); }),
    ]);
  }, [client]);
  const acceptStatus = useCallback(async (value: LegacyOrder) => {
    if (!mounted.current) return;
    const before = acceptedOrder.current;
    const refunded = refundAmount(value);
    if (before?.order_id === value.order_id) {
      const priorRefund = refundAmount(before);
      if ((before.status === "refunded" && value.status !== "refunded") ||
          (priorRefund !== null && priorRefund > 0 && (refunded === null || refunded < priorRefund))) {
        throw new Error("The original order status could not be verified.");
      }
    }
    acceptedOrder.current = value;
    setOrder(value);
    if ((value.status === "paid" && value.fulfilled) || value.status === "refunded" || (refunded !== null && refunded > 0)) {
      setError(null);
      setMessage(value.status === "refunded" ? "The original payment was refunded."
        : refunded ? "The original payment was partially refunded."
          : "Payment confirmed. Your original purchased entitlements were delivered.");
      setReconciliationHeld(false);
      window.dispatchEvent(new Event("refresh_analysis_units"));
      await refreshBalances(true);
    }
  }, [refreshBalances]);
  const readRecent = useCallback((afterResolution = false) => {
    const read = ++recentReadId.current;
    return apiGet<LegacyOrder | null>("/billing/recent-order").then(async (value) => {
      if (!mounted.current || recentReadId.current !== read) return;
      if (value && (typeof value.sku !== "string" || isUnifiedPassSku(value.sku))) throw new Error("The original order needs billing support review.");
      if (value && (!afterResolution || blocksNewPurchase(value))) await acceptStatus(value);
      if (mounted.current && recentReadId.current === read) setRecentRead("ready");
    }).catch(() => {
      if (mounted.current && recentReadId.current === read) {
        setRecentRead("failed"); setError("Could not verify previous purchases. Check again before starting another payment.");
      }
    });
  }, [acceptStatus]);

  const acceptRecoveryStatus = useCallback(async (previous: LegacyOrder, value: LegacyOrder) => {
    if (!mounted.current) return;
    const refresh = blocksNewPurchase(previous) && !blocksNewPurchase(value);
    if (refresh) {
      recentReadId.current += 1;
      setRecentRead("loading");
      setIndiaConfirmed(false);
    }
    await acceptStatus(value);
    if (refresh && mounted.current) await readRecent(true);
  }, [acceptStatus, readRecent]);

  useEffect(() => {
    mounted.current = true;
    void Promise.allSettled([refreshBalances(), readRecent()]);
    return () => { mounted.current = false; pollId.current += 1; recentReadId.current += 1; balanceRead.current += 1; statusWork.current?.abort(); statusWork.current = null; };
  }, [readRecent, refreshBalances]);

  const checkStatus = useCallback(async (original: LegacyOrder, automatic = false) => {
    if (!mounted.current || statusWork.current || (!automatic && actionPending.current)) return;
    const orderId = original.order_id;
    const work = new AbortController(); statusWork.current = work;
    const run = ++pollId.current;
    const budget = statusBudgets.current.get(orderId) ?? newStatusReadBudget();
    statusBudgets.current.set(orderId, budget);
    setBusy(true); setError(null);
    try {
      const outcome = await boundedStatusChecks({ budget, signal: work.signal, automatic,
        read: async (signal) => {
          const value = await apiGet<LegacyOrder>(`/billing/orders/${encodeURIComponent(orderId)}`, signal);
          signal.throwIfAborted();
          if (!mounted.current || pollId.current !== run) throw new DOMException("Status check stopped", "AbortError");
          if (value.order_id !== orderId || value.sku !== original.sku
            || (original.accepted_quote && !sameQuote(value.accepted_quote, original.accepted_quote))) throw new Error("The original order could not be verified.");
          await acceptRecoveryStatus(original, value);
          if (!automatic && mounted.current && !signal.aborted) {
            setCheckoutStatusRequired((heldOrder) => heldOrder === orderId ? null : heldOrder);
          }
          return value;
        }, active: blocksNewPurchase });
      if (mounted.current && pollId.current === run && outcome !== "settled") setMessage("Payment is still pending. Automatic checks are paused; use Check payment status for one fresh read. If you were charged, do not pay again.");
    } catch (failure) { if (mounted.current && pollId.current === run && !work.signal.aborted) setError(errorText(failure, "Could not check the original payment.")); }
    finally {
      if (statusWork.current === work) statusWork.current = null;
      if (mounted.current && pollId.current === run) setBusy(false);
    }
  }, [acceptRecoveryStatus]);

  async function reconcile() {
    if (!order || !canReconcile(order) || busy || actionPending.current || statusWork.current) return;
    actionPending.current = true; setBusy(true); setIndiaConfirmed(false); setReconciliationHeld(true); setError(null);
    try {
      const result = await apiPostJson<{ resolution: string; message: string; order: LegacyOrder }>(`/billing/orders/${encodeURIComponent(order.order_id)}/reconcile`, undefined);
      if (!mounted.current) return;
      if (!["unresolved", "checkout_ready", "settled"].includes(result.resolution)
        || result.order.order_id !== order.order_id || result.order.sku !== order.sku
        || (order.accepted_quote && !sameQuote(result.order.accepted_quote, order.accepted_quote))
        || (result.resolution === "checkout_ready" && (result.order.status !== "pending" || !result.order.provider_order_id || !result.order.accepted_quote || result.order.checkout_available !== true))
        || (result.resolution === "settled" && !["paid", "refunded"].includes(result.order.status))) throw new Error("The original payment could not be verified. Checkout remains paused.");
      await acceptRecoveryStatus(order, result.order);
      if (result.resolution !== "unresolved") {
        setCheckoutStatusRequired((heldOrder) => heldOrder === order.order_id ? null : heldOrder);
      }
      setReconciliationHeld(result.resolution === "unresolved"); setMessage(result.message);
    } catch (failure) { if (mounted.current) setError(errorText(failure, "Could not reconcile the original payment.")); }
    finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }

  async function resume() {
    if (!order || !isFiniteBundleSku(order.sku) || order.status !== "pending" || !order.accepted_quote || !order.provider_order_id || order.checkout_available !== true
      || !indiaConfirmed || busy || reconciliationHeld || checkoutStatusRequired === order.order_id || actionPending.current || statusWork.current) return;
    actionPending.current = true; setBusy(true); setError(null);
    const accepted = order.accepted_quote;
    if (intent.current?.orderId !== order.order_id) intent.current = { orderId: order.order_id, key: crypto.randomUUID() };
    try {
      const result = await apiPostJson<LegacyCheckout>("/billing/orders", { sku: order.sku, billing_country: "IN",
        accepted_quote: accepted, idempotency_key: intent.current.key, order_reference: order.order_id });
      if (!mounted.current) return;
      if (result.order_id !== order.order_id || result.provider !== "razorpay" || result.provider_order_id !== order.provider_order_id
        || !result.key_id || result.currency !== accepted.currency || result.amount_minor !== accepted.amount_minor
        || !sameQuote(result.accepted_quote, accepted)) throw new Error("The original checkout terms could not be verified. No checkout opened.");
      const Razorpay = await loadHostedCheckout();
      if (!mounted.current) return;
      let callbackReceived = false;
      let checkoutDismissed = false;
      const checkout = new Razorpay({ key: result.key_id, amount: result.amount_minor, currency: result.currency,
        name: result.name, description: result.description, order_id: result.provider_order_id, prefill: result.prefill,
        handler: (callback) => {
          if (callbackReceived || !mounted.current) return;
          callbackReceived = true;
          void apiPostJson(`/billing/orders/${encodeURIComponent(order.order_id)}/checkout-result`, callback).catch(() => {});
          if (!checkoutDismissed) void checkStatus(order, true);
        },
        modal: { confirm_close: true, ondismiss: () => {
          if (callbackReceived || checkoutDismissed || !mounted.current) return;
          checkoutDismissed = true;
          setCheckoutStatusRequired(order.order_id);
          setIndiaConfirmed(false);
          setBusy(false); setMessage("Checkout closed. No payment is assumed. Check the original payment status before trying again.");
        } }, retry: { enabled: true }, theme: { color: "#485c11" },
      });
      checkout.on("payment.failed", () => {
        if (!callbackReceived && !checkoutDismissed && mounted.current) setMessage("The payment attempt was not completed. You can retry in checkout or close it to check the original payment.");
      });
      checkout.open();
    } catch (failure) {
      if (!mounted.current) return;
      if (failure instanceof HostedCheckoutLoadError) {
        setCheckoutStatusRequired(order.order_id);
        setIndiaConfirmed(false);
      }
      const detail = failure instanceof ApiError ? (failure.detail as { detail?: { code?: string; order_id?: string } })?.detail : null;
      if (detail?.code === "billing_order_requires_status" && detail.order_id === order.order_id) {
        try {
          const original = await apiGet<LegacyOrder>(`/billing/orders/${encodeURIComponent(order.order_id)}`);
          if (original.order_id !== order.order_id || original.sku !== order.sku) throw new Error("Original payment needs support review.");
          await acceptRecoveryStatus(order, original);
          setBusy(false);
          if (original.status === "paid" && original.fulfilled) return;
        } catch { setReconciliationHeld(true); }
      }
      setBusy(false); setError(errorText(failure, "Could not resume the original checkout."));
    }
    finally { actionPending.current = false; }
  }

  const refunded = order ? refundAmount(order) : null;
  const analysisBalance = signedBalance(profile?.ai_credits);
  return <section className="space-y-6 border-t border-border pt-8" aria-labelledby="legacy-entitlements-heading">
    <div><p className="data-label">Earlier purchases</p><h2 id="legacy-entitlements-heading" className="font-display mt-2 text-2xl">Legacy entitlements</h2>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">These balances retain their original promises. They remain separate from your new credit wallet until an approved migration is complete; they have not been added together or converted.</p></div>
    <div className="grid gap-5 sm:grid-cols-3" aria-label="Current entitlement" role="region">
      <div><p className="data-label">Analysis units</p><p className="mt-2 text-2xl font-semibold">{analysisBalance === null ? profile ? "Unavailable" : "..." : Math.max(0, analysisBalance)}</p>{analysisBalance !== null && analysisBalance < 0 ? <p className="mt-1 text-xs">Pending analysis-unit adjustment: {-analysisBalance}.</p> : null}</div>
      <div><p className="data-label">Job service credits</p><p className="mt-2 text-2xl font-semibold">{serviceCredits === undefined ? "..." : serviceCredits === null ? "Unavailable" : Math.max(0, serviceCredits)}</p>{serviceCredits === null ? <p className="mt-1 text-xs">Could not verify your available balance. Refresh this page to try again.</p> : serviceCredits !== undefined && serviceCredits < 0 ? <p className="mt-1 text-xs">Pending credit adjustment: {-serviceCredits}.</p> : null}</div>
      <div><p className="data-label">Current access</p><p className="mt-2 text-lg">{profile?.tier === "premium" ? "Premium active" : profile?.tier === "free" ? "Free access" : "Checking status"}</p>{profile?.premium_until ? <p className="text-xs">Through {new Date(profile.premium_until).toLocaleDateString("en-IN")}; no automatic renewal.</p> : null}</div>
    </div>
    {error ? <p role="alert" className="text-sm text-coral">{error}</p> : null}
    {recentRead === "failed" ? <Button variant="secondary" onClick={() => { setRecentRead("loading"); void readRecent(); }}>Check previous purchases</Button> : null}
    <p className="text-xs leading-5 text-muted-foreground">Payment confirmation updates while this tab is visible. If checks pause, use Check payment status before trying another payment.</p>
    {message ? <p role="status" className="text-sm leading-6">{message}</p> : null}
    {order ? <section className="surface-panel space-y-4 p-5" aria-labelledby="legacy-payment-heading">
      <h3 id="legacy-payment-heading" className="font-display text-xl">{order.status === "refunded" ? "Payment refunded" : refunded ? "Payment partially refunded" : order.status === "paid" && order.fulfilled ? "Payment confirmed" : "Previous payment"}</h3>
      <p className="break-all text-xs">Order reference: {order.order_id}</p>
      {refunded ? <p className="text-sm">Total refunded: {formatCreditPrice(refunded)}</p> : null}
      {order.accepted_quote ? <div aria-label="Original checkout terms" className="text-sm leading-6"><p>Original order: {order.accepted_quote.job_service_credits} service credits + {order.accepted_quote.analysis_units} analysis units for {formatCreditPrice(order.accepted_quote.amount_minor)}.</p><p>Resuming accepts these original terms and checks this same order.</p></div> : <p className="text-sm">Original pack terms need review. Checking status is free; contact billing support before resuming payment.</p>}
      {order.status === "pending" ? <>
        {!order.provider_order_id ? <p className="text-sm">Checkout creation has an unknown outcome. Payment remains paused; check status or contact billing support to reconcile the original attempt.</p> : null}
        <label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" checked={indiaConfirmed} disabled={busy} onChange={(event) => setIndiaConfirmed(event.target.checked)} className="mt-1 h-4 w-4 accent-primary" /><span>I confirm my billing country is India and this original INR purchase is for domestic use.</span></label>
      </> : null}
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" disabled={busy} onClick={() => void checkStatus(order)}>Check payment status</Button>
        {canReconcile(order) ? <Button variant="secondary" disabled={busy} onClick={() => void reconcile()}>Reconcile original payment</Button> : null}
        {order.status === "pending" ? <Button disabled={busy || reconciliationHeld || checkoutStatusRequired === order.order_id || !indiaConfirmed || !isFiniteBundleSku(order.sku) || !order.accepted_quote || !order.provider_order_id || order.checkout_available !== true} onClick={() => void resume()}>Resume original checkout</Button> : null}
        <Link href="/contact" className="inline-flex min-h-11 items-center text-sm underline">Billing support</Link>
      </div>
    </section> : null}
    <ServiceCreditHistory refreshKey={order && (order.fulfilled || order.status === "refunded" || refunded) ? `${order.order_id}:${order.status}:${refunded ?? ""}` : ""} />
    <details className="border-t border-border pt-5"><summary className="cursor-pointer font-semibold">Legacy analysis history</summary>
      {usage ? usage.items.length ? <ul className="mt-4 divide-y divide-border">{usage.items.map((item) => <li key={item.id} className="flex flex-wrap justify-between gap-3 py-3 text-sm"><span>{item.event_type.replaceAll("_", " ")} · {new Date(item.created_at).toLocaleDateString("en-IN")}</span><span>{item.amount > 0 ? "+" : ""}{item.amount} units · balance {item.balance_after}</span></li>)}</ul> : <p className="mt-3 text-sm">No legacy analysis-unit activity yet.</p> : <p className="mt-3 text-sm">Analysis history is currently unavailable.</p>}
    </details>
  </section>;
}
