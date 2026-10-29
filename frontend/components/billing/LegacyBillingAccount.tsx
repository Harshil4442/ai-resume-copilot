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
import { loadHostedCheckout } from "./hostedCheckout";

type LegacyQuote = { catalog_version: string; amount_minor: number; currency: "INR"; job_service_credits: number; analysis_units: number };
type LegacyOrder = {
  order_id: string; provider_order_id?: string | null; sku: string; status: "pending" | "paid" | "failed" | "refunded";
  fulfilled: boolean; amount_minor: number; currency: "INR"; accepted_quote?: LegacyQuote | null;
  checkout_available?: boolean; payment_reference?: string | null;
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
function balance(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}
function errorText(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback; }

/** Existing purchases remain usable on their original terms; this component never sells a new legacy pack. */
export function LegacyBillingAccount({ onPendingChange }: { onPendingChange: (pending: boolean) => void }) {
  const client = useQueryClient();
  const statusWork = useRef<AbortController | null>(null);
  const statusBudgets = useRef(new Map<string, StatusReadBudget>());
  const mounted = useRef(true), pollId = useRef(0), actionPending = useRef(false);
  const intent = useRef<{ orderId: string; key: string } | null>(null);
  const [profile, setProfile] = useState<{ tier?: string; ai_credits?: number; premium_until?: string | null } | null>(null);
  const [serviceCredits, setServiceCredits] = useState<number | null | undefined>(undefined);
  const [usage, setUsage] = useState<UsageHistory | null>(null);
  const [order, setOrder] = useState<LegacyOrder | null>(null);
  const [recentRead, setRecentRead] = useState<"loading" | "ready" | "failed">("loading");
  const [busy, setBusy] = useState(false), [indiaConfirmed, setIndiaConfirmed] = useState(false);
  const [reconciliationHeld, setReconciliationHeld] = useState(false);
  const [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  const pending = recentRead !== "ready" || order?.status === "pending" || (order?.status === "paid" && !order.fulfilled);
  useEffect(() => { onPendingChange(pending); }, [pending, onPendingChange]);

  const refreshBalances = useCallback(async (force = false) => {
    await Promise.allSettled([
      fetchOwnedProfile(client, force).then((value) => {
        if (mounted.current) setProfile(value);
      }),
      apiGet<{ job_service_credits?: unknown }>("/auth/me").then((value) => {
        if (mounted.current) setServiceCredits(balance(value.job_service_credits));
      }).catch(() => { if (mounted.current) setServiceCredits(null); }),
      apiGet<UsageHistory>("/v1/usage-events?limit=20").then((value) => { if (mounted.current) setUsage(value); }),
    ]);
  }, [client]);
  const acceptStatus = useCallback(async (value: LegacyOrder) => {
    if (!mounted.current) return;
    setOrder(value);
    if (value.status === "paid" && value.fulfilled) {
      setError(null); setMessage("Payment confirmed. Your original purchased entitlements were delivered.");
      setReconciliationHeld(false);
      window.dispatchEvent(new Event("refresh_analysis_units"));
      await refreshBalances(true);
    } else if (value.status === "refunded") {
      setReconciliationHeld(false); setMessage("The original payment was refunded.");
    }
  }, [refreshBalances]);
  const readRecent = useCallback(() => apiGet<LegacyOrder | null>("/billing/recent-order").then(async (value) => {
    if (!mounted.current) return;
    if (value && (typeof value.sku !== "string" || isUnifiedPassSku(value.sku))) throw new Error("The original order needs billing support review.");
    if (value) await acceptStatus(value);
    if (mounted.current) setRecentRead("ready");
  }).catch(() => {
    if (mounted.current) { setRecentRead("failed"); setError("Could not verify previous purchases. Check again before starting another payment."); }
  }), [acceptStatus]);

  useEffect(() => {
    mounted.current = true;
    void Promise.allSettled([refreshBalances(), readRecent()]);
    return () => { mounted.current = false; pollId.current += 1; statusWork.current?.abort(); };
  }, [readRecent, refreshBalances]);

  const checkStatus = useCallback(async (orderId: string, automatic = false) => {
    if (!mounted.current) return;
    statusWork.current?.abort();
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
          if (value.order_id !== orderId || typeof value.sku !== "string" || isUnifiedPassSku(value.sku)) throw new Error("The original order could not be verified.");
          await acceptStatus(value); return value;
        }, active: (value) => value.status === "pending" || (value.status === "paid" && !value.fulfilled) });
      if (mounted.current && pollId.current === run && outcome !== "settled") setMessage("Payment is still pending. Automatic checks are paused; use Check payment status for one fresh read. If you were charged, do not pay again.");
    } catch (failure) { if (mounted.current && pollId.current === run && !work.signal.aborted) setError(errorText(failure, "Could not check the original payment.")); }
    finally { if (mounted.current && pollId.current === run) setBusy(false); }
  }, [acceptStatus]);

  async function reconcile() {
    if (!order || order.status !== "pending" || busy || actionPending.current) return;
    actionPending.current = true; setBusy(true); setIndiaConfirmed(false); setReconciliationHeld(true); setError(null);
    try {
      const result = await apiPostJson<{ resolution: string; message: string; order: LegacyOrder }>(`/billing/orders/${encodeURIComponent(order.order_id)}/reconcile`, undefined);
      if (!mounted.current) return;
      if (!["unresolved", "checkout_ready", "settled"].includes(result.resolution)
        || result.order.order_id !== order.order_id || result.order.sku !== order.sku
        || (order.accepted_quote && !sameQuote(result.order.accepted_quote, order.accepted_quote))
        || (result.resolution === "checkout_ready" && (result.order.status !== "pending" || !result.order.provider_order_id || !result.order.accepted_quote || result.order.checkout_available !== true))
        || (result.resolution === "settled" && !["paid", "refunded"].includes(result.order.status))) throw new Error("The original payment could not be verified. Checkout remains paused.");
      await acceptStatus(result.order);
      setReconciliationHeld(result.resolution === "unresolved"); setMessage(result.message);
    } catch (failure) { if (mounted.current) setError(errorText(failure, "Could not reconcile the original payment.")); }
    finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }

  async function resume() {
    if (!order || !isFiniteBundleSku(order.sku) || order.status !== "pending" || !order.accepted_quote || !order.provider_order_id || order.checkout_available !== true
      || !indiaConfirmed || busy || reconciliationHeld || actionPending.current) return;
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
      await loadHostedCheckout();
      if (!mounted.current || !window.Razorpay) return;
      let callbackReceived = false;
      const checkout = new window.Razorpay({ key: result.key_id, amount: result.amount_minor, currency: result.currency,
        name: result.name, description: result.description, order_id: result.provider_order_id, prefill: result.prefill,
        handler: (callback) => {
          if (callbackReceived || !mounted.current) return;
          callbackReceived = true;
          void apiPostJson(`/billing/orders/${encodeURIComponent(order.order_id)}/checkout-result`, callback).catch(() => {});
          void checkStatus(order.order_id, true);
        },
        modal: { confirm_close: true, ondismiss: () => { if (!callbackReceived && mounted.current) {
          setBusy(false); setMessage("Checkout closed. No payment is assumed. Check the original payment status before trying again.");
        } } }, retry: { enabled: true }, theme: { color: "#485c11" },
      });
      checkout.on("payment.failed", () => { if (mounted.current) { setBusy(false); setMessage("The attempt was not completed. Check the original payment before retrying."); } });
      checkout.open();
    } catch (failure) {
      if (!mounted.current) return;
      const detail = failure instanceof ApiError ? (failure.detail as { detail?: { code?: string; order_id?: string } })?.detail : null;
      if (detail?.code === "billing_order_requires_status" && detail.order_id === order.order_id) {
        try {
          const original = await apiGet<LegacyOrder>(`/billing/orders/${encodeURIComponent(order.order_id)}`);
          if (original.order_id !== order.order_id || original.sku !== order.sku) throw new Error("Original payment needs support review.");
          await acceptStatus(original);
          setBusy(false);
          if (original.status === "paid" && original.fulfilled) return;
        } catch { setReconciliationHeld(true); }
      }
      setBusy(false); setError(errorText(failure, "Could not resume the original checkout."));
    }
    finally { actionPending.current = false; }
  }

  return <section className="space-y-6 border-t border-border pt-8" aria-labelledby="legacy-entitlements-heading">
    <div><p className="data-label">Earlier purchases</p><h2 id="legacy-entitlements-heading" className="font-display mt-2 text-2xl">Legacy entitlements</h2>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">These balances retain their original promises. They remain separate from your new credit wallet until an approved migration is complete; they have not been added together or converted.</p></div>
    <div className="grid gap-5 sm:grid-cols-3" aria-label="Current entitlement" role="region">
      <div><p className="data-label">Analysis units</p><p className="mt-2 text-2xl font-semibold">{profile ? balance(profile.ai_credits) ?? "Unavailable" : "..."}</p></div>
      <div><p className="data-label">Job service credits</p><p className="mt-2 text-2xl font-semibold">{serviceCredits === undefined ? "..." : serviceCredits ?? "Unavailable"}</p>{serviceCredits === null ? <p className="mt-1 text-xs">Could not verify your available balance. Refresh this page to try again.</p> : null}</div>
      <div><p className="data-label">Current access</p><p className="mt-2 text-lg">{profile?.tier === "premium" ? "Premium active" : profile?.tier === "free" ? "Free access" : "Checking status"}</p>{profile?.premium_until ? <p className="text-xs">Through {new Date(profile.premium_until).toLocaleDateString("en-IN")}; no automatic renewal.</p> : null}</div>
    </div>
    {error ? <p role="alert" className="text-sm text-coral">{error}</p> : null}
    {recentRead === "failed" ? <Button variant="secondary" onClick={() => { setRecentRead("loading"); void readRecent(); }}>Check previous purchases</Button> : null}
    <p className="text-xs leading-5 text-muted-foreground">After checkout, at most 8 automatic payment status reads run with increasing delays. Checks pause in a hidden tab or on capacity errors; manual status checks read once.</p>
    {message ? <p role="status" className="text-sm leading-6">{message}</p> : null}
    {order ? <section className="surface-panel space-y-4 p-5" aria-labelledby="legacy-payment-heading">
      <h3 id="legacy-payment-heading" className="font-display text-xl">{order.status === "paid" && order.fulfilled ? "Payment confirmed" : "Previous payment"}</h3>
      <p className="break-all text-xs">Order reference: {order.order_id}</p>
      {order.accepted_quote ? <div aria-label="Original checkout terms" className="text-sm leading-6"><p>Original order: {order.accepted_quote.job_service_credits} service credits + {order.accepted_quote.analysis_units} analysis units for {formatCreditPrice(order.accepted_quote.amount_minor)}.</p><p>Resuming accepts these original terms and checks this same order.</p></div> : <p className="text-sm">Original pack terms need review. Checking status is free; contact billing support before resuming payment.</p>}
      {order.status === "pending" ? <>
        {!order.provider_order_id ? <p className="text-sm">Checkout creation has an unknown outcome. Payment remains paused; check status or contact billing support to reconcile the original attempt.</p> : null}
        <label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" checked={indiaConfirmed} disabled={busy} onChange={(event) => setIndiaConfirmed(event.target.checked)} className="mt-1 h-4 w-4 accent-primary" /><span>I confirm my billing country is India and this original INR purchase is for domestic use.</span></label>
      </> : null}
      <div className="flex flex-wrap gap-3">
        <Button variant="secondary" disabled={busy} onClick={() => void checkStatus(order.order_id)}>Check payment status</Button>
        {order.status === "pending" ? <><Button variant="secondary" disabled={busy} onClick={() => void reconcile()}>Reconcile original payment</Button>
          <Button disabled={busy || reconciliationHeld || !indiaConfirmed || !isFiniteBundleSku(order.sku) || !order.accepted_quote || !order.provider_order_id || order.checkout_available !== true} onClick={() => void resume()}>Resume original checkout</Button></> : null}
        <Link href="/contact" className="inline-flex min-h-11 items-center text-sm underline">Billing support</Link>
      </div>
    </section> : null}
    <ServiceCreditHistory refreshKey={order?.fulfilled ? order.order_id : ""} />
    <details className="border-t border-border pt-5"><summary className="cursor-pointer font-semibold">Legacy analysis history</summary>
      {usage ? usage.items.length ? <ul className="mt-4 divide-y divide-border">{usage.items.map((item) => <li key={item.id} className="flex flex-wrap justify-between gap-3 py-3 text-sm"><span>{item.event_type.replaceAll("_", " ")} · {new Date(item.created_at).toLocaleDateString("en-IN")}</span><span>{item.amount > 0 ? "+" : ""}{item.amount} units · balance {item.balance_after}</span></li>)}</ul> : <p className="mt-3 text-sm">No legacy analysis-unit activity yet.</p> : <p className="mt-3 text-sm">Analysis history is currently unavailable.</p>}
    </details>
  </section>;
}
