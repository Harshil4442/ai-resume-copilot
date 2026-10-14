"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  CircleGauge,
  Clock3,
  CreditCard,
  Crown,
  ExternalLink,
  FileText,
  History,
  Lock,
  RefreshCw,
  Shield,
} from "lucide-react";
import { ApiError, apiGet, apiPostJson } from "../../lib/api";
import type { components } from "../../lib/generated/api";
import { Button } from "../../components/ui/Button";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { ServiceCreditHistory } from "../../components/billing/ServiceCreditHistory";
import { trackEvent } from "../../lib/analytics";

const RAZORPAY_CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";
const SUPPORTED_SKUS = ["starter_bundle", "growth_bundle", "scale_bundle"] as const;
const CONFIRMATION_ATTEMPTS = 24;
const CONFIRMATION_INTERVAL_MS = 2500;

type SupportedSku = (typeof SUPPORTED_SKUS)[number];
type CheckoutPhase = "idle" | "opening" | "confirming" | "pending" | "confirmed";
type PurchaseQuoteAcceptance = components["schemas"]["PurchaseQuoteAcceptance"];

interface BillingProduct {
  sku: SupportedSku;
  name: string;
  description: string;
  amount_minor: number;
  amount_display: string;
  currency: "INR";
  billing_type: "one_time";
  duration_days: number;
  entitlement_kind: "credit_bundle" | "premium_access" | "job_service_credits";
  analysis_units: number;
  entitlement_quantity: number;
  auto_renews: false;
  catalog_visible: boolean;
  enabled_for_purchase: boolean;
}

interface BillingCatalog {
  catalog_version: string;
  market: "IN";
  checkout_enabled: boolean;
  provider: "razorpay" | null;
  products: BillingProduct[];
  availability_message?: string | null;
}

type CreateOrderResponse = components["schemas"]["CheckoutOrderResponse"];

function acceptedPurchaseQuote(catalog: BillingCatalog, product: BillingProduct): PurchaseQuoteAcceptance {
  return { catalog_version: catalog.catalog_version, amount_minor: product.amount_minor,
    currency: product.currency, job_service_credits: product.entitlement_quantity, analysis_units: product.analysis_units };
}

function samePurchaseQuote(actual: PurchaseQuoteAcceptance | undefined, accepted: PurchaseQuoteAcceptance): boolean {
  return actual !== undefined && actual.catalog_version === accepted.catalog_version &&
    actual.amount_minor === accepted.amount_minor && actual.currency === accepted.currency &&
    actual.job_service_credits === accepted.job_service_credits && actual.analysis_units === accepted.analysis_units;
}

const needsPurchaseReview = (error: unknown) => error instanceof ApiError &&
  (error.detail as { detail?: { code?: string } } | null)?.detail?.code === "billing_quote_requires_review";

interface BillingOrderStatus {
  order_id: string;
  provider_order_id?: string | null;
  payment_reference?: string | null;
  sku: string;
  status: "pending" | "paid" | "failed" | "refunded";
  fulfilled: boolean;
  amount_minor: number;
  currency: "INR";
  created_at: string;
  paid_at: string | null;
  refunded_at: string | null;
  accepted_quote?: PurchaseQuoteAcceptance | null;
  checkout_available?: boolean;
}

type ReconciliationResponse = components["schemas"]["ReconciliationResponse"];

interface UsageEvent {
  id: string;
  analysis_run_id: string | null;
  event_type: string;
  amount: number;
  balance_after: number;
  source_type: string;
  source_id: string | null;
  reason: string | null;
  created_at: string;
}

interface UsageHistoryResponse {
  balance: number;
  items: UsageEvent[];
}

interface RazorpaySuccessResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

interface RazorpayFailureResponse {
  error?: {
    description?: string;
  };
}

interface RazorpayOptions {
  key: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  order_id: string;
  prefill?: {
    email?: string;
  };
  handler: (response: RazorpaySuccessResponse) => void;
  modal: {
    confirm_close: boolean;
    ondismiss: () => void;
  };
  retry: {
    enabled: boolean;
  };
  theme: {
    color: string;
  };
}

interface RazorpayCheckout {
  open: () => void;
  on: (event: "payment.failed", callback: (response: RazorpayFailureResponse) => void) => void;
}

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => RazorpayCheckout;
  }
}

let checkoutScriptPromise: Promise<void> | null = null;

function isSupportedSku(value: unknown): value is SupportedSku {
  return typeof value === "string" && SUPPORTED_SKUS.includes(value as SupportedSku);
}

function loadHostedCheckout(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Checkout is available only in a browser."));
  }
  if (window.Razorpay) return Promise.resolve();
  if (checkoutScriptPromise) return checkoutScriptPromise;

  checkoutScriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = RAZORPAY_CHECKOUT_SRC;
    script.async = true;
    script.onload = () => {
      if (window.Razorpay) {
        resolve();
      } else {
        checkoutScriptPromise = null;
        reject(new Error("The hosted checkout did not initialize."));
      }
    };
    script.onerror = () => {
      checkoutScriptPromise = null;
      script.remove();
      reject(new Error("The hosted checkout could not be loaded."));
    };
    document.body.appendChild(script);
  });

  return checkoutScriptPromise;
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatPrice(amountMinor: number, currency: string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    maximumFractionDigits: amountMinor % 100 === 0 ? 0 : 2,
  }).format(amountMinor / 100);
}

function productFacts(product: BillingProduct): string[] {
  return [
    `${product.entitlement_quantity} job service credits`,
    `${product.analysis_units} AI analysis units · full tailoring uses 2 units`,
    "New verified employer jobs use job service credits",
    "Current application pilot: reviewed assistance, manual final submission",
    "Optional AI uses its own displayed analysis-unit quote",
    "Finite prepaid balances · one-time payment · no automatic renewal",
  ];
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function usageEventLabel(event: UsageEvent): string {
  if (event.event_type === "reserve") return "Analysis units reserved";
  if (event.event_type === "commit") return "Analysis completed";
  if (event.event_type === "release") return "Reserved units returned";
  if (event.event_type === "waive") return "Included with current access";
  if (event.event_type === "adjust") return "Support adjustment";
  return event.event_type.replaceAll("_", " ");
}

function BillingContent({ requestedSku }: { requestedSku: string | null }) {
  const mountedRef = useRef(true);
  const pollRunRef = useRef(0);
  const confirmedOrderRef = useRef(new Set<string>());
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [priceRefreshRequired, setPriceRefreshRequired] = useState(false);
  const [selection, setSelection] = useState<{ sku: SupportedSku; querySku: string | null } | null>(null);
  const [currentTier, setCurrentTier] = useState<string | null>(null);
  const [premiumUntil, setPremiumUntil] = useState<string | null>(null);
  const [analysisUnits, setAnalysisUnits] = useState(0);
  const [serviceCredits, setServiceCredits] = useState(0);
  const [usageHistory, setUsageHistory] = useState<UsageHistoryResponse | null>(null);
  const [usageLoading, setUsageLoading] = useState(true);
  const [indiaBillingConfirmed, setIndiaBillingConfirmed] = useState(false);
  const [phase, setPhase] = useState<CheckoutPhase>("idle");
  const [currentOrder, setCurrentOrder] = useState<BillingOrderStatus | null>(null);
  const [reconciliation, setReconciliation] = useState<{ orderId: string; resolution: ReconciliationResponse["resolution"]; message: string } | null>(null);
  const [reconciliationPending, setReconciliationPending] = useState(false);
  const purchaseIntent = useRef<{ fingerprint: string; key: string; sku: SupportedSku; acceptedQuote: PurchaseQuoteAcceptance } | null>(null);
  const [recoverablePurchase, setRecoverablePurchase] = useState<{ sku: SupportedSku; acceptedQuote: PurchaseQuoteAcceptance } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshProfile = useCallback(async () => {
    const profile = await apiGet<{ tier?: string; ai_credits?: number; job_service_credits?: number; premium_until?: string | null }>("/auth/profile");
    if (!mountedRef.current) return;
    setCurrentTier(profile.tier || "free");
    setAnalysisUnits(profile.ai_credits ?? 0);
    setServiceCredits(profile.job_service_credits ?? 0);
    setPremiumUntil(profile.premium_until || null);
  }, []);

  const refreshUsage = useCallback(async () => {
    try {
      const response = await apiGet<UsageHistoryResponse>("/v1/usage-events?limit=20");
      if (!mountedRef.current) return;
      setUsageHistory(response);
      setAnalysisUnits(response.balance);
    } finally {
      if (mountedRef.current) setUsageLoading(false);
    }
  }, []);

  const refreshCatalog = useCallback(async () => {
    const response = await apiGet<BillingCatalog>("/public/billing/catalog");
    if (!mountedRef.current) return;
    if (response.market !== "IN" || typeof response.catalog_version !== "string") {
      throw new Error("The India billing catalog is temporarily unavailable.");
    }
    const products = Array.isArray(response.products)
      ? response.products.filter(
          (product): product is BillingProduct =>
            Boolean(product) &&
            isSupportedSku(product.sku) &&
            product.currency === "INR" &&
            product.billing_type === "one_time" &&
            product.auto_renews === false &&
            Number.isInteger(product.amount_minor) &&
            product.amount_minor > 0,
        )
      : [];
    setCatalog({ ...response, products });
    setPriceRefreshRequired(false);
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    refreshCatalog()
      .catch((catalogError: unknown) => {
        if (!mountedRef.current) return;
        setError(
          getErrorMessage(
            catalogError,
            "Billing information is temporarily unavailable. No checkout has been started.",
          ),
        );
      })
      .finally(() => {
        if (mountedRef.current) setCatalogLoading(false);
      });

    refreshProfile().catch(() => {
      // Authentication middleware owns signed-out navigation. A catalog can
      // still be shown safely if profile refresh is momentarily unavailable.
    });
    refreshUsage().catch(() => {
      // The current balance still comes from the profile if history is unavailable.
    });

    return () => {
      mountedRef.current = false;
      pollRunRef.current += 1;
    };
  }, [refreshCatalog, refreshProfile, refreshUsage]);

  const visibleProducts = useMemo(() => {
    if (!catalog) return [];
    return catalog.products.filter((product) => product.catalog_visible && isSupportedSku(product.sku));
  }, [catalog]);

  const selectedSku = selection?.querySku === requestedSku
    ? selection.sku
    : isSupportedSku(requestedSku) ? requestedSku : null;
  const effectiveSelectedSku =
    selectedSku && visibleProducts.some((product) => product.sku === selectedSku)
      ? selectedSku
      : visibleProducts[0]?.sku ?? null;

  const catalogProduct = useMemo(
    () => visibleProducts.find((product) => product.sku === effectiveSelectedSku) ?? null,
    [effectiveSelectedSku, visibleProducts],
  );
  const retainedQuote = recoverablePurchase?.sku === catalogProduct?.sku ? recoverablePurchase?.acceptedQuote : null;
  const selectedProduct = useMemo(() => catalogProduct && retainedQuote ? { ...catalogProduct, amount_minor: retainedQuote.amount_minor,
    entitlement_quantity: retainedQuote.job_service_credits, analysis_units: retainedQuote.analysis_units } : catalogProduct,
  [catalogProduct, retainedQuote]);

  const checkoutEnabled = Boolean(
    catalog?.checkout_enabled && catalog.provider === "razorpay",
  );
  const purchaseAllowed = Boolean(
    checkoutEnabled &&
      selectedProduct?.enabled_for_purchase &&
      !priceRefreshRequired &&
      currentOrder === null &&
      currentTier !== null &&
      indiaBillingConfirmed,
  );
  const checkoutBusy = phase === "opening" || phase === "confirming";
  const reconciliationHeld = reconciliation?.orderId === currentOrder?.order_id && reconciliation?.resolution === "unresolved";

  const markConfirmed = useCallback(
    async (order: BillingOrderStatus) => {
      if (!mountedRef.current) return;
      setCurrentOrder(order);
      setPhase("confirmed");
      setError(null);
      if (!confirmedOrderRef.current.has(order.order_id)) {
        confirmedOrderRef.current.add(order.order_id);
        trackEvent("entitlement_fulfilled", {
          $insert_id: `entitlement-${order.order_id}`,
          order_id: order.order_id,
          sku: order.sku,
          amount_minor: order.amount_minor,
          currency: order.currency,
        });
      }
      window.dispatchEvent(new Event("refresh_analysis_units"));
      try {
        await refreshProfile();
      } catch {
        // The verified order status is authoritative. A later profile refresh
        // will pick up the entitlement if this request briefly fails.
      }
    },
    [refreshProfile],
  );

  useEffect(() => {
    let cancelled = false;
    apiGet<BillingOrderStatus | null>("/billing/recent-order")
      .then(async (order) => {
        if (cancelled || !mountedRef.current) return;
        if (!order) return;
        if (order.status === "paid" && order.fulfilled) {
          await markConfirmed(order);
          return;
        }
        setCurrentOrder(order);
        if (order.status === "pending") setPhase("pending");
      })
      .catch(() => {
        // No recent order is the normal first-purchase state.
      });
    return () => {
      cancelled = true;
    };
  }, [markConfirmed]);

  const pollOrder = useCallback(
    async (orderId: string) => {
      const run = ++pollRunRef.current;
      setPhase("confirming");
      setError(null);

      for (let attempt = 0; attempt < CONFIRMATION_ATTEMPTS; attempt += 1) {
        if (!mountedRef.current || pollRunRef.current !== run) return;

        try {
          const order = await apiGet<BillingOrderStatus>(`/billing/orders/${encodeURIComponent(orderId)}`);
          if (!mountedRef.current || pollRunRef.current !== run) return;
          setCurrentOrder(order);

          if (order.status === "paid" && order.fulfilled) {
            await markConfirmed(order);
            return;
          }
          if (order.status === "failed") {
            setPhase("idle");
            setError("The payment was not completed. Your HireWiz access was not changed.");
            return;
          }
          if (order.status === "refunded") {
            setPhase("idle");
            setReconciliation((previous) => previous?.orderId === order.order_id
              ? { ...previous, resolution: "settled", message: "The original payment was refunded." } : previous);
            setError("This order was refunded and did not activate paid access.");
            return;
          }
        } catch (statusError: unknown) {
          if (attempt === CONFIRMATION_ATTEMPTS - 1) {
            setError(
              getErrorMessage(
                statusError,
                "We could not read the order status. Contact support if your account is not updated.",
              ),
            );
          }
        }

        await wait(CONFIRMATION_INTERVAL_MS);
      }

      if (!mountedRef.current || pollRunRef.current !== run) return;
      setPhase("pending");
      setError(
        "Payment confirmation is taking longer than usual. If you were charged, do not pay again. Use “Check payment status” below or contact billing support with the order ID.",
      );
    },
    [markConfirmed],
  );

  const handleCheckout = useCallback(async (recoverOrder?: BillingOrderStatus) => {
    if (checkoutBusy || reconciliationPending) return;
    if (recoverOrder ? !recoverOrder.accepted_quote || !indiaBillingConfirmed || !checkoutEnabled :
      !catalog || !selectedProduct || !purchaseAllowed) return;
    if (recoverOrder && (!recoverOrder.provider_order_id || recoverOrder.checkout_available !== true)) return;
    if (recoverOrder && reconciliation?.orderId === recoverOrder.order_id && reconciliation.resolution === "unresolved") return;
    const targetSku = recoverOrder?.sku ?? selectedProduct?.sku;
    if (!isSupportedSku(targetSku)) return;
    const acceptedQuote = recoverOrder?.accepted_quote ?? retainedQuote ?? acceptedPurchaseQuote(catalog!, selectedProduct!);
    const fingerprint = JSON.stringify({ sku: targetSku, accepted_quote: acceptedQuote });
    if (purchaseIntent.current?.fingerprint !== fingerprint) purchaseIntent.current = {
      fingerprint, key: crypto.randomUUID(), sku: targetSku, acceptedQuote,
    };
    const requestIdentity = purchaseIntent.current.key;

    setPhase("opening");
    setError(null);
    if (!recoverOrder) setCurrentOrder(null);
    try {
      // Acceptance binds the displayed total and both included allowances.
      // The server validates it before creating any new checkout attempt.
      const order = await apiPostJson<CreateOrderResponse>("/billing/orders", {
        sku: targetSku,
        billing_country: "IN",
        accepted_quote: acceptedQuote,
        idempotency_key: requestIdentity,
        ...(recoverOrder ? { order_reference: recoverOrder.order_id } : {}),
      });

      if (
        order.provider !== "razorpay" ||
        !order.order_id ||
        !order.provider_order_id ||
        !order.key_id ||
        order.currency !== acceptedQuote.currency ||
        order.amount_minor !== acceptedQuote.amount_minor ||
        !samePurchaseQuote(order.accepted_quote, acceptedQuote)
      ) {
        throw new Error("Checkout configuration changed. Refresh this page before trying again.");
      }

      trackEvent("checkout_started", {
        sku: targetSku,
        amount_minor: acceptedQuote.amount_minor,
        currency: acceptedQuote.currency,
        provider: order.provider,
      });

      setCurrentOrder({
        order_id: order.order_id,
        provider_order_id: order.provider_order_id,
        checkout_available: true,
        sku: targetSku,
        status: "pending",
        fulfilled: false,
        amount_minor: order.amount_minor,
        currency: order.currency,
        created_at: new Date().toISOString(),
        paid_at: null,
        refunded_at: null,
        accepted_quote: acceptedQuote,
      });
      setRecoverablePurchase(null);

      // The provider script is loaded only after the backend confirms that
      // checkout is approved, enabled and configured, and creates an order.
      await loadHostedCheckout();
      if (!window.Razorpay) throw new Error("The hosted checkout is unavailable.");

      let successReported = false;
      const checkout = new window.Razorpay({
        key: order.key_id,
        amount: order.amount_minor,
        currency: order.currency,
        name: order.name,
        description: order.description,
        order_id: order.provider_order_id,
        prefill: order.prefill?.email ? { email: order.prefill.email } : undefined,
        handler: (response) => {
          successReported = true;
          trackEvent("payment_client_confirmed", {
            sku: targetSku,
            order_id: order.order_id,
            provider_order_id: response.razorpay_order_id,
          });
          setPhase("confirming");
          // Send the provider callback fields for server-side HMAC verification,
          // but never treat this browser callback as fulfilment authority. The
          // verified webhook remains the only path that can grant access.
          void apiPostJson(`/billing/orders/${encodeURIComponent(order.order_id)}/checkout-result`, {
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_order_id: response.razorpay_order_id,
            razorpay_signature: response.razorpay_signature,
          })
            .catch(() => {
              // A delayed callback-verification request must not tempt the user
              // to pay twice. Status polling can still observe the webhook.
              if (mountedRef.current) {
                setError("Checkout returned successfully. Waiting for verified server confirmation…");
              }
            });
          void pollOrder(order.order_id);
        },
        modal: {
          confirm_close: true,
          ondismiss: () => {
            if (successReported) return;
            trackEvent("checkout_dismissed", {
              sku: targetSku,
              order_id: order.order_id,
            });
            setPhase("pending");
            setError(
              "Checkout was closed. No payment is assumed. If you completed a payment, check the server status before trying again.",
            );
          },
        },
        retry: { enabled: true },
        theme: { color: "#485c11" },
      });

      checkout.on("payment.failed", (response) => {
        trackEvent("checkout_failed", {
          sku: targetSku,
          order_id: order.order_id,
          failure_category: "provider_failed",
        });
        setPhase("pending");
        setError(
          response.error?.description ||
            "The payment attempt was not completed. Your HireWiz access was not changed.",
        );
      });
      checkout.open();
    } catch (checkoutError: unknown) {
      trackEvent("checkout_failed", {
        sku: targetSku,
        failure_category: "initialization_failed",
      });
      setPhase("idle");
      const recovery = checkoutError instanceof ApiError ?
        (checkoutError.detail as { detail?: { code?: string; order_id?: string } } | null)?.detail : undefined;
      if (recovery?.code === "billing_order_requires_status" && recovery.order_id) {
        setRecoverablePurchase(null);
        try {
          const original = await apiGet<BillingOrderStatus>(`/billing/orders/${encodeURIComponent(recovery.order_id)}`);
          if (!mountedRef.current) return;
          setCurrentOrder(original);
          if (original.status === "paid" && original.fulfilled) await markConfirmed(original);
          else { setPhase(original.status === "pending" ? "pending" : "idle"); setError(getErrorMessage(checkoutError, "Review your original order below. Checking its status is free and does not create another purchase.")); }
        } catch {
          if (mountedRef.current) { setCurrentOrder(recoverOrder ?? { order_id: recovery.order_id, sku: targetSku, status: "pending", fulfilled: false,
            amount_minor: acceptedQuote.amount_minor, currency: acceptedQuote.currency, created_at: new Date().toISOString(),
            paid_at: null, refunded_at: null, accepted_quote: null });
            setPhase("pending"); setError("Could not read the original payment status. Use Check payment status before continuing."); }
        }
        return;
      }
      if (needsPurchaseReview(checkoutError)) {
        purchaseIntent.current = null; setRecoverablePurchase(null);
        setIndiaBillingConfirmed(false);
        setPriceRefreshRequired(true);
        setCatalog(null);
        setCatalogLoading(true);
        try {
          await refreshCatalog();
          if (mountedRef.current) setError("Pack terms were refreshed. Review the price and included credits, confirm India billing again, then click Pay when ready.");
        } catch {
          if (mountedRef.current) setError("Could not refresh pack terms. Refresh the catalog before reviewing another purchase. No new checkout was created.");
        } finally {
          if (mountedRef.current) setCatalogLoading(false);
        }
        return;
      }
      if (purchaseIntent.current) setRecoverablePurchase({ sku: purchaseIntent.current.sku, acceptedQuote: purchaseIntent.current.acceptedQuote });
      setError(
        getErrorMessage(
          checkoutError,
          "Checkout could not be started. No payment was initiated.",
        ),
      );
    }
  }, [catalog, checkoutBusy, checkoutEnabled, indiaBillingConfirmed, markConfirmed, pollOrder, purchaseAllowed, reconciliation, reconciliationPending, refreshCatalog, retainedQuote, selectedProduct]);

  const reconcileCurrentOrder = useCallback(async () => {
    if (!currentOrder || currentOrder.status !== "pending" || checkoutBusy || reconciliationPending) return;
    const orderId = currentOrder.order_id;
    pollRunRef.current += 1;
    setReconciliationPending(true);
    setIndiaBillingConfirmed(false);
    setError(null);
    setReconciliation({ orderId, resolution: "unresolved", message: "Checking the original payment. Checkout remains paused." });
    try {
      const result = await apiPostJson<ReconciliationResponse>(`/billing/orders/${encodeURIComponent(orderId)}/reconcile`, undefined);
      if (!mountedRef.current) return;
      if (!["unresolved", "checkout_ready", "settled"].includes(result.resolution) ||
        result.order.order_id !== orderId || result.order.sku !== currentOrder.sku ||
        (currentOrder.accepted_quote && !samePurchaseQuote(result.order.accepted_quote ?? undefined, currentOrder.accepted_quote)) ||
        (result.resolution === "checkout_ready" && (result.order.status !== "pending" || !result.order.provider_order_id || !result.order.accepted_quote || result.order.checkout_available !== true)) ||
        (result.resolution === "settled" && !["paid", "refunded"].includes(result.order.status))) {
        throw new Error("The original payment could not be verified. Checkout remains paused; contact billing support.");
      }
      setCurrentOrder(result.order);
      setReconciliation({ orderId, resolution: result.resolution, message: result.message });
      if (result.resolution === "settled" && result.order.status === "paid" && result.order.fulfilled) {
        await markConfirmed(result.order);
      } else {
        setPhase(result.order.status === "pending" || result.order.status === "paid" ? "pending" : "idle");
        if (result.resolution === "settled" && result.order.status === "refunded") {
          setError("This order was refunded and did not activate paid access.");
        }
      }
    } catch (reconciliationError: unknown) {
      if (mountedRef.current) setReconciliation({ orderId, resolution: "unresolved", message: getErrorMessage(reconciliationError,
        "Could not reconcile the original payment. Checkout remains paused; check status or contact billing support.") });
    } finally {
      if (mountedRef.current) setReconciliationPending(false);
    }
  }, [checkoutBusy, currentOrder, markConfirmed, reconciliationPending]);

  const checkCurrentOrder = useCallback(() => {
    if (currentOrder?.order_id && !checkoutBusy && !reconciliationPending) void pollOrder(currentOrder.order_id);
  }, [checkoutBusy, currentOrder, pollOrder, reconciliationPending]);

  if (phase === "confirmed" && currentOrder) {
    return (
      <main className="app-page">
        <div className="page-container max-w-3xl">
          <section className="border-y border-primary/30 py-12 text-center" aria-labelledby="payment-confirmed-heading">
            <span className="icon-tile mx-auto h-14 w-14 text-primary"><CheckCircle2 size={27} /></span>
            <p className="eyebrow mt-6">Verified fulfilment</p>
            <h1 id="payment-confirmed-heading" className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Payment confirmed</h1>
            <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
              The server received verified payment confirmation and delivered your purchased {"entitlements"}.
            </p>
            <div className="mx-auto mt-6 max-w-xl border-y border-border py-4 text-xs leading-5 text-muted-foreground">
              <p className="break-all">Order reference: {currentOrder.order_id}</p>
              {currentOrder.payment_reference ? <p className="break-all">Payment reference: {currentOrder.payment_reference}</p> : null}
            </div>
            <div className="mt-7 flex flex-col justify-center gap-3 sm:flex-row">
              <Button asChild><Link href={"/employer-jobs"}>Continue to {"employer jobs"}</Link></Button>
              <Button variant="secondary" onClick={() => { setPhase("idle"); setCurrentOrder(null); setIndiaBillingConfirmed(false); purchaseIntent.current = null; setRecoverablePurchase(null); setReconciliation(null); }}>Review another purchase</Button>
              <Button asChild variant="ghost"><Link href="/contact">Billing support</Link></Button>
            </div>
          </section>
        </div>
      </main>
    );
  }

  return (
    <main className="app-page">
      <div className="page-container space-y-10">
        <header className="grid gap-6 border-b border-border pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="eyebrow">Account billing</p>
            <h1 className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Your prepaid balances</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Choose one of three finite packs. Each includes job service credits and separate AI analysis units, with no automatic renewal.</p>
          </div>
          <div className="flex items-center gap-3">
            <span className="icon-tile">{currentTier === "premium" ? <Crown size={19} className="text-primary" /> : <CircleGauge size={19} />}</span>
            <div><p className="data-label">Current access</p><p className="mt-1 font-semibold text-foreground">{currentTier === "premium" ? "Premium active" : currentTier === "free" ? "Free access" : "Checking status"}</p></div>
          </div>
        </header>

        <section className="grid gap-5 border-y border-border py-6 sm:grid-cols-3" aria-label="Current entitlement">
          <div><p className="data-label">Analysis units</p><p className="mt-2 text-3xl font-semibold text-primary">{currentTier ? analysisUnits : "..."}</p><p className="mt-1 text-xs text-muted-foreground">{currentTier === "premium" ? "Existing paid access terms remain; new AI requests depend on available funding." : "Finite balance after reservations and releases."}</p></div>
          <div className="sm:border-l sm:border-border sm:pl-6"><p className="data-label">Job service credits</p><p className="mt-2 text-3xl font-semibold text-primary">{currentTier ? serviceCredits : "..."}</p><p className="mt-1 text-xs text-muted-foreground">New verified employer jobs. Premium does not waive search charges. Automatic submission is unavailable in the current pilot.</p></div>
          <div className="sm:border-l sm:border-border sm:pl-6"><p className="data-label">Access period</p><p className="mt-2 text-lg font-semibold text-foreground">{currentTier === "premium" && premiumUntil ? `Through ${new Date(premiumUntil).toLocaleDateString("en-IN")}` : currentTier === "premium" ? "Active" : "Free tier"}</p><p className="mt-1 text-xs text-muted-foreground">Premium ends automatically. No renewal payment is scheduled.</p></div>
        </section>

        {error ? <div role="alert" className="flex items-start gap-3 border-y border-coral/30 bg-coral/5 px-4 py-4 text-sm text-coral"><AlertCircle size={18} className="mt-0.5 shrink-0" /><span>{error}{priceRefreshRequired ? <button type="button" className="ml-2 underline" disabled={catalogLoading} onClick={async () => { setCatalogLoading(true); try { await refreshCatalog(); setError("Review the refreshed price and included credits, confirm India billing, then click Pay when ready."); } catch { setError("Could not refresh pack terms. Try refreshing the catalog again."); } finally { if (mountedRef.current) setCatalogLoading(false); } }}>Refresh catalog</button> : null}</span></div> : null}

        {catalogLoading ? <LoadingBlock rows={6} /> : (
          <div className="grid gap-10 lg:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)] lg:items-start">
            <section aria-labelledby="products-heading">
              <div><p className="data-label">Available products</p><h2 id="products-heading" className="font-display mt-2 text-2xl font-normal text-foreground">Choose your purchase</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Price and entitlement terms come from the server catalog.</p></div>
              <div className="mt-6 grid gap-4">
                {visibleProducts.length ? visibleProducts.map((product) => {
                  const selected = product.sku === effectiveSelectedSku;
                  return (
                    <article key={product.sku} className={`surface-panel p-5 sm:p-7 ${selected ? "border-primary/70" : ""}`}>
                      <div className="grid gap-6 sm:grid-cols-[1fr_auto] sm:items-start">
                        <div className="min-w-0">
                          <div className="flex items-start gap-3"><span className="icon-tile">{product.entitlement_kind === "credit_bundle" ? <CreditCard size={19} className="text-primary" /> : <Crown size={19} className="text-primary" />}</span><div><h3 className="text-xl font-semibold text-foreground">{product.name}</h3><p className="mt-1 text-xs font-bold uppercase text-muted-foreground">One-time purchase | no renewal</p></div></div>
                          <p className="mt-5 text-sm leading-6 text-muted-foreground">{product.description}</p>
                          <ul className="mt-5 grid gap-2.5">{productFacts(product).map((fact) => <li key={fact} className="flex items-start gap-2 text-sm text-muted-foreground"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-primary" /><span>{fact}</span></li>)}</ul>
                        </div>
                        <div className="sm:min-w-44 sm:text-right"><p className="text-3xl font-semibold text-primary">{formatPrice(product.amount_minor, product.currency)}</p><p className="mt-1 text-xs text-muted-foreground">INR total</p><Button type="button" variant={selected ? "secondary" : "primary"} className="mt-4 w-full transition-transform" onClick={() => { setSelection({ sku: product.sku, querySku: requestedSku }); trackEvent("checkout_product_selected", { sku: product.sku, amount_minor: product.amount_minor, currency: product.currency }); }} disabled={currentTier === null}>{currentTier === null ? "Checking status" : selected ? "Selected" : "Review purchase"}</Button></div>
                      </div>
                    </article>
                  );
                }) : <div className="border-y border-border py-8"><p className="font-bold text-foreground">No paid product is currently available.</p><p className="mt-2 text-sm text-muted-foreground">No checkout provider has been loaded.</p></div>}
              </div>
              <div className="mt-8 border-y border-border py-6 text-sm leading-6 text-muted-foreground"><h3 className="font-semibold text-foreground">About analysis units</h3><p className="mt-2">Analysis units are software usage allowances, not money or stored value. They cannot be withdrawn, resold, or transferred. Job service credits are a separate prepaid allowance for newly delivered employer jobs. The current application pilot provides reviewed preparation and supported text assistance with manual attachment and final submission; it has no automatic-application fee. Optional AI always has a separate reviewed quote. Free and legacy access AI work can pause when its finite funding pool is exhausted.</p><Link href="/pricing" className="mt-3 inline-flex items-center gap-1 font-bold text-primary hover:underline">Read full pricing and usage details <ExternalLink size={13} /></Link></div>
            </section>

            <section aria-labelledby="checkout-heading">
              <div><p className="data-label">Purchase review</p><h2 id="checkout-heading" className="font-display mt-2 text-2xl font-normal text-foreground">Order summary</h2><p className="mt-2 text-sm text-muted-foreground">Review the amount, seller, delivery, and renewal terms.</p></div>
              <div className="surface-panel mt-6 p-5 sm:p-7">
                {selectedProduct ? <div>
                  <div className={`border-l-2 pl-4 ${checkoutEnabled ? "border-primary" : "border-primary/30"}`}><div className="flex items-start gap-3">{checkoutEnabled ? <Shield className="mt-0.5 shrink-0 text-primary" size={18} /> : <Clock3 className="mt-0.5 shrink-0 text-primary" size={18} />}<div><h3 className="font-semibold text-foreground">{checkoutEnabled ? "Secure checkout available" : "Checkout is not available yet"}</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">{checkoutEnabled ? "Confirm India billing, then continue to Razorpay hosted checkout." : catalog?.availability_message || "The product remains visible while purchases are paused."}</p></div></div></div>
                  <div className="mt-6 grid grid-cols-[1fr_auto] gap-4 border-y border-border py-5"><div><p className="data-label">Product</p><p className="mt-2 font-semibold text-foreground">{selectedProduct.name}</p></div><div className="text-right"><p className="data-label">Due today</p><p className="mt-2 text-2xl font-semibold text-primary">{formatPrice(selectedProduct.amount_minor, selectedProduct.currency)}</p></div></div>
                  <dl className="divide-y divide-border text-sm">{[["Purchase type", "One-time payment"], ["Automatic renewal", "No"], ["Trial", "None"], ["Billing country", "India"], ["Seller", "HireWiz, operated by SAVALIYA HARSHIL YOGESHBHAI"], ["Delivery", `${selectedProduct.entitlement_quantity} service credits + ${selectedProduct.analysis_units} analysis units after verified payment`]].map(([term, value]) => <div key={term} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] gap-4 py-3"><dt className="text-muted-foreground">{term}</dt><dd className="text-right font-semibold text-foreground">{value}</dd></div>)}</dl>
                  <p className="border-y border-border py-4 text-xs leading-5 text-muted-foreground">The server locks this total before checkout. Compare the same amount in the hosted payment window before authorizing payment.{retainedQuote ? " Checking the interrupted request at its originally accepted price and included credits; retrying retains that request identity." : ""}</p>
                  <label className="mt-5 flex cursor-pointer items-start gap-3 text-sm leading-6 text-muted-foreground"><input type="checkbox" checked={indiaBillingConfirmed} onChange={(event) => setIndiaBillingConfirmed(event.target.checked)} disabled={currentTier === null || !checkoutEnabled} className="mt-1 h-4 w-4 shrink-0 accent-primary" /><span>I confirm my billing country is India and this INR purchase is for domestic use.</span></label>
                  <div className="mt-5">
                    {currentTier === null ? <div className="flex items-start gap-3 border-y border-border py-4 text-sm text-muted-foreground"><Lock size={17} className="mt-0.5 shrink-0" />Checkout waits for account status.</div> : checkoutEnabled && selectedProduct.enabled_for_purchase ? <Button type="button" onClick={() => void handleCheckout()} disabled={!purchaseAllowed || checkoutBusy || reconciliationPending} className="w-full">{checkoutBusy ? <><RefreshCw size={17} className="animate-spin" />{phase === "confirming" ? "Waiting for verified confirmation" : "Opening hosted checkout"}</> : <><CreditCard size={17} />Pay with Razorpay | {formatPrice(selectedProduct.amount_minor, selectedProduct.currency)}</>}</Button> : <Button type="button" variant="secondary" disabled className="w-full">Checkout unavailable</Button>}
                  </div>
                </div> : <div className="py-8 text-center"><FileText className="mx-auto text-muted-foreground" size={28} /><p className="mt-3 font-bold text-foreground">Select a product to review it.</p></div>}

                {currentOrder && phase !== "confirmed" ? <div className="mt-6 border-t border-border pt-5 text-xs text-muted-foreground">
                  <p className="break-all">Order reference: {currentOrder.order_id}</p>
                  {currentOrder.payment_reference ? <p className="break-all">Payment reference: {currentOrder.payment_reference}</p> : null}
                  {currentOrder.accepted_quote ? <div className="mt-3 leading-5" aria-label="Original checkout terms">
                    <p>Original order: {currentOrder.accepted_quote.job_service_credits} service credits + {currentOrder.accepted_quote.analysis_units} analysis units for {formatPrice(currentOrder.accepted_quote.amount_minor, currentOrder.accepted_quote.currency)}.</p>
                    <p className="break-all">Original catalog: {currentOrder.accepted_quote.catalog_version}. Resuming accepts these original terms and checks this same order.</p>
                  </div> : <p className="mt-3 leading-5">Original pack terms need review. Checking status is free; contact billing support before resuming payment.</p>}
                  {currentOrder.status === "pending" && !currentOrder.provider_order_id ? <p className="mt-3 leading-5">Checkout creation has an unknown outcome. Payment remains paused. Check status or contact billing support to reconcile the original attempt before another purchase.</p> : null}
                  {currentOrder.status === "pending" && currentOrder.provider_order_id && currentOrder.checkout_available !== true ? <p className="mt-3 leading-5">Payment remains paused. Reconcile this original payment or contact billing support before resuming checkout.</p> : null}
                  {reconciliation?.orderId === currentOrder.order_id ? <div role="status" className="mt-3 leading-5">
                    <p>{reconciliation.message}</p>
                    {reconciliation.resolution === "checkout_ready" ? <p className="mt-2">Review the original terms above, confirm India billing again, then click Resume original checkout when ready.</p> : null}
                  </div> : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button type="button" variant="secondary" size="sm" onClick={checkCurrentOrder} disabled={checkoutBusy || reconciliationPending}><RefreshCw size={13} className={phase === "confirming" ? "animate-spin" : ""} />Check payment status</Button>
                    {currentOrder.status === "pending" ? <>
                      <Button type="button" variant="secondary" size="sm" className="min-h-11" onClick={() => void reconcileCurrentOrder()} disabled={checkoutBusy || reconciliationPending} aria-busy={reconciliationPending}>{reconciliationPending ? <RefreshCw size={13} className="animate-spin" /> : null}{reconciliationPending ? "Reconciling original payment" : "Reconcile original payment"}</Button>
                      <Button type="button" variant="secondary" size="sm" onClick={() => void handleCheckout(currentOrder)} disabled={currentOrder.checkout_available !== true || !currentOrder.provider_order_id || !currentOrder.accepted_quote || !indiaBillingConfirmed || !checkoutEnabled || checkoutBusy || reconciliationPending || reconciliationHeld}>Resume original checkout</Button>
                    </> : currentOrder.status === "failed" || currentOrder.status === "refunded" ? <Button type="button" variant="secondary" size="sm" disabled={reconciliationPending || reconciliationHeld} onClick={() => { purchaseIntent.current = null; setRecoverablePurchase(null); setCurrentOrder(null); setIndiaBillingConfirmed(false); setError(null); setPhase("idle"); setReconciliation(null); }}>Review another purchase</Button> : null}
                  </div>
                </div> : null}
              </div>

              <div className="mt-6 border-y border-border py-5 text-sm leading-6 text-muted-foreground"><div className="flex items-start gap-3"><Shield size={18} className="mt-0.5 shrink-0 text-primary" /><p>Card, CVV, UPI PIN, bank-login, and other payment credentials are entered only in Razorpay hosted checkout.</p></div><div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 border-t border-border pt-4 text-xs font-bold">{[["/terms", "Terms"], ["/privacy", "Privacy"], ["/refund", "Refunds"], ["/digital-delivery", "Digital delivery"], ["/contact", "Billing support"]].map(([href, label]) => <Link key={href} href={href} className="text-primary hover:underline">{label}</Link>)}</div></div>
            </section>
          </div>
        )}

        <ServiceCreditHistory refreshKey={currentOrder?.fulfilled ? currentOrder.order_id : ""} />

        <section className="border-t border-border pt-8" aria-labelledby="usage-history-heading">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="data-label">Analysis units</p><h2 id="usage-history-heading" className="font-display mt-2 flex items-center gap-2 text-2xl font-normal text-foreground"><History size={20} className="text-primary" /> Analysis history</h2><p className="mt-2 text-sm text-muted-foreground">AI reservations, completed operations, legacy access events, and automatic releases.</p></div><p className="text-sm font-bold text-foreground">Current balance: <span className="text-primary">{analysisUnits}</span> units</p></div>
          {usageLoading ? <div className="mt-6"><LoadingBlock rows={4} /></div> : usageHistory?.items.length ? <div className="mt-6 divide-y divide-border border-y border-border">{usageHistory.items.map((event) => <div key={event.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_auto_auto] sm:items-center sm:gap-6"><div><p className="text-sm font-bold text-foreground">{usageEventLabel(event)}</p><p className="mt-1 text-xs text-muted-foreground">{new Date(event.created_at).toLocaleString("en-IN")}{event.analysis_run_id ? ` | Run ${event.analysis_run_id.slice(-8)}` : ""}</p></div><p className={`text-sm font-semibold ${event.amount > 0 ? "text-primary" : event.amount < 0 ? "text-primary" : "text-muted-foreground"}`}>{event.amount > 0 ? "+" : ""}{event.amount} units</p><p className="text-xs text-muted-foreground sm:text-right">Balance {event.balance_after}</p></div>)}</div> : <p className="mt-6 border-y border-border py-7 text-sm text-muted-foreground">No analysis-unit activity yet.</p>}
        </section>
      </div>
    </main>
  );
}

function BillingQuery() {
  const searchParams = useSearchParams();
  return <BillingContent requestedSku={searchParams.get("sku")} />;
}

export default function BillingPage() {
  return <Suspense fallback={<main className="app-page"><div className="page-container"><LoadingBlock rows={5} /></div></main>}><BillingQuery /></Suspense>;
}
