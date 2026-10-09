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
import { apiGet, apiPostJson } from "../../lib/api";
import { Button } from "../../components/ui/Button";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { trackEvent } from "../../lib/analytics";
import { finiteCreditBundles, isFiniteBundleSku, type CreditBundleProduct, type FiniteBundleSku } from "../../lib/billingProducts";

const RAZORPAY_CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";
const CONFIRMATION_ATTEMPTS = 24;
const CONFIRMATION_INTERVAL_MS = 2500;

type CheckoutPhase = "idle" | "opening" | "confirming" | "pending" | "confirmed";

type BillingProduct = CreditBundleProduct;

interface BillingCatalog {
  catalog_version: string;
  market: "IN";
  checkout_enabled: boolean;
  provider: "razorpay" | null;
  products: BillingProduct[];
}

interface CreateOrderResponse {
  order_id: string;
  provider: "razorpay";
  provider_order_id: string;
  key_id: string;
  amount_minor: number;
  currency: "INR";
  name: string;
  description: string;
  prefill?: {
    email?: string;
  };
}

interface BillingOrderStatus {
  order_id: string;
  payment_reference?: string | null;
  sku: string;
  status: "pending" | "paid" | "failed" | "refunded";
  fulfilled: boolean;
  amount_minor: number;
  currency: "INR";
  created_at: string;
  paid_at: string | null;
  refunded_at: string | null;
}

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
    `${product.analysis_units} AI analysis units`,
    `${product.job_service_credits} job service credits`,
    "Finite prepaid balances",
    "One-time payment with no automatic renewal",
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
  const [selection, setSelection] = useState<{ sku: FiniteBundleSku; querySku: string | null } | null>(null);
  const [currentTier, setCurrentTier] = useState<string | null>(null);
  const [premiumUntil, setPremiumUntil] = useState<string | null>(null);
  const [analysisUnits, setAnalysisUnits] = useState(0);
  const [serviceCredits, setServiceCredits] = useState(0);
  const [usageHistory, setUsageHistory] = useState<UsageHistoryResponse | null>(null);
  const [usageLoading, setUsageLoading] = useState(true);
  const [indiaBillingConfirmed, setIndiaBillingConfirmed] = useState(false);
  const [phase, setPhase] = useState<CheckoutPhase>("idle");
  const [currentOrder, setCurrentOrder] = useState<BillingOrderStatus | null>(null);
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

  useEffect(() => {
    mountedRef.current = true;

    apiGet<BillingCatalog>("/public/billing/catalog")
      .then((response) => {
        if (!mountedRef.current) return;
        if (response.market !== "IN" || typeof response.catalog_version !== "string") {
          throw new Error("The India billing catalog is temporarily unavailable.");
        }
        const products = finiteCreditBundles(response.products);
        setCatalog({ ...response, products });
      })
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
  }, [refreshProfile, refreshUsage]);

  const visibleProducts = useMemo(() => {
    if (!catalog) return [];
    return catalog.products.filter((product) => product.catalog_visible);
  }, [catalog]);

  const selectedSku = selection?.querySku === requestedSku
    ? selection.sku
    : isFiniteBundleSku(requestedSku) ? requestedSku : null;
  const effectiveSelectedSku =
    selectedSku && visibleProducts.some((product) => product.sku === selectedSku)
      ? selectedSku
      : visibleProducts[0]?.sku ?? null;

  const selectedProduct = useMemo(
    () => visibleProducts.find((product) => product.sku === effectiveSelectedSku) ?? null,
    [effectiveSelectedSku, visibleProducts],
  );

  const checkoutEnabled = Boolean(
    catalog?.checkout_enabled === true && catalog.provider === "razorpay",
  );
  const purchaseAllowed = Boolean(
    checkoutEnabled &&
      selectedProduct?.enabled_for_purchase &&
      currentTier !== null &&
      indiaBillingConfirmed,
  );
  const checkoutBusy = phase === "opening" || phase === "confirming";

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

  const handleCheckout = useCallback(async () => {
    if (!selectedProduct || !purchaseAllowed || checkoutBusy) return;

    setPhase("opening");
    setError(null);
    setCurrentOrder(null);
    try {
      // The browser sends only an allowlisted SKU. Amount, currency and
      // entitlements are selected and locked by the server.
      const order = await apiPostJson<CreateOrderResponse>("/billing/orders", {
        sku: selectedProduct.sku,
        billing_country: "IN",
      });

      if (
        order.provider !== "razorpay" ||
        !order.order_id ||
        !order.provider_order_id ||
        !order.key_id ||
        order.currency !== selectedProduct.currency ||
        order.amount_minor !== selectedProduct.amount_minor
      ) {
        throw new Error("Checkout configuration changed. Refresh this page before trying again.");
      }

      trackEvent("checkout_started", {
        sku: selectedProduct.sku,
        amount_minor: selectedProduct.amount_minor,
        currency: selectedProduct.currency,
        provider: order.provider,
      });

      setCurrentOrder({
        order_id: order.order_id,
        sku: selectedProduct.sku,
        status: "pending",
        fulfilled: false,
        amount_minor: order.amount_minor,
        currency: order.currency,
        created_at: new Date().toISOString(),
        paid_at: null,
        refunded_at: null,
      });

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
            sku: selectedProduct.sku,
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
              sku: selectedProduct.sku,
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
          sku: selectedProduct.sku,
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
        sku: selectedProduct.sku,
        failure_category: "initialization_failed",
      });
      setPhase("idle");
      setError(
        getErrorMessage(
          checkoutError,
          "Checkout could not be started. No payment was initiated.",
        ),
      );
    }
  }, [checkoutBusy, pollOrder, purchaseAllowed, selectedProduct]);

  const checkCurrentOrder = useCallback(() => {
    if (currentOrder?.order_id) void pollOrder(currentOrder.order_id);
  }, [currentOrder, pollOrder]);

  if (phase === "confirmed" && currentOrder) {
    return (
      <main className="app-page">
        <div className="page-container max-w-3xl">
          <section className="border-y border-primary/30 py-12 text-center" aria-labelledby="payment-confirmed-heading">
            <span className="icon-tile mx-auto h-14 w-14 text-primary"><CheckCircle2 size={27} /></span>
            <p className="eyebrow mt-6">Verified fulfilment</p>
            <h1 id="payment-confirmed-heading" className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Payment confirmed</h1>
            <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
              The server received verified payment confirmation and delivered your purchased {currentOrder.sku === "premium_30d" ? "Premium access" : currentOrder.sku === "job_service_500" ? "job service credits" : "analysis units and job service credits"}.
            </p>
            <div className="mx-auto mt-6 max-w-xl border-y border-border py-4 text-xs leading-5 text-muted-foreground">
              <p className="break-all">Order reference: {currentOrder.order_id}</p>
              {currentOrder.payment_reference ? <p className="break-all">Payment reference: {currentOrder.payment_reference}</p> : null}
            </div>
            <div className="mt-7 flex flex-col justify-center gap-3 sm:flex-row">
              <Button asChild><Link href={currentOrder.sku === "job_service_500" ? "/employer-jobs" : "/dashboard"}>Continue to {currentOrder.sku === "job_service_500" ? "employer jobs" : "dashboard"}</Link></Button>
              <Button variant="secondary" onClick={() => { setPhase("idle"); setCurrentOrder(null); setIndiaBillingConfirmed(false); }}>Review another purchase</Button>
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
            <h1 className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Access and service credits</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Review finite credit bundles for AI analysis and employer job services. Purchases use one-time INR payments with no automatic renewal.</p>
          </div>
          <div className="flex items-center gap-3">
            <span className="icon-tile">{currentTier === "premium" ? <Crown size={19} className="text-primary" /> : <CircleGauge size={19} />}</span>
            <div><p className="data-label">Current access</p><p className="mt-1 font-semibold text-foreground">{currentTier === "premium" ? "Premium active" : currentTier === "free" ? "Free access" : "Checking status"}</p></div>
          </div>
        </header>

        <section className="grid gap-5 border-y border-border py-6 sm:grid-cols-3" aria-label="Current entitlement">
          <div><p className="data-label">Analysis units</p><p className="mt-2 text-3xl font-semibold text-primary">{currentTier === "premium" ? "Included" : currentTier === "free" ? analysisUnits : "..."}</p><p className="mt-1 text-xs text-muted-foreground">{currentTier === "premium" ? "No unit deductions while Premium is active." : "Durable balance after reservations and releases."}</p></div>
          <div className="sm:border-l sm:border-border sm:pl-6"><p className="data-label">Job service credits</p><p className="mt-2 text-3xl font-semibold text-primary">{currentTier ? serviceCredits : "..."}</p><p className="mt-1 text-xs text-muted-foreground">Employer search and automatic apply. Charged separately, including for Premium members.</p></div>
          <div className="sm:border-l sm:border-border sm:pl-6"><p className="data-label">Access period</p><p className="mt-2 text-lg font-semibold text-foreground">{currentTier === "premium" && premiumUntil ? `Through ${new Date(premiumUntil).toLocaleDateString("en-IN")}` : currentTier === "premium" ? "Active" : "Free tier"}</p><p className="mt-1 text-xs text-muted-foreground">Premium ends automatically. No renewal payment is scheduled.</p></div>
        </section>

        {error ? <div role="alert" className="flex items-start gap-3 border-y border-coral/30 bg-coral/5 px-4 py-4 text-sm text-coral"><AlertCircle size={18} className="mt-0.5 shrink-0" /><span>{error}</span></div> : null}

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
                          <div className="flex items-start gap-3"><span className="icon-tile"><CreditCard size={19} className="text-primary" /></span><div><h3 className="text-xl font-semibold text-foreground">{product.name}</h3><p className="mt-1 text-xs font-bold uppercase text-muted-foreground">One-time purchase | no renewal</p></div></div>
                          <p className="mt-5 text-sm leading-6 text-muted-foreground">{product.description}</p>
                          <ul className="mt-5 grid gap-2.5">{productFacts(product).map((fact) => <li key={fact} className="flex items-start gap-2 text-sm text-muted-foreground"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-primary" /><span>{fact}</span></li>)}</ul>
                        </div>
                        <div className="sm:min-w-44 sm:text-right"><p className="text-3xl font-semibold text-primary">{formatPrice(product.amount_minor, product.currency)}</p><p className="mt-1 text-xs text-muted-foreground">INR total</p><Button type="button" variant={selected ? "secondary" : "primary"} className="mt-4 w-full" onClick={() => { setSelection({ sku: product.sku, querySku: requestedSku }); trackEvent("checkout_product_selected", { sku: product.sku, amount_minor: product.amount_minor, currency: product.currency }); }} disabled={currentTier === null}>{currentTier === null ? "Checking status" : selected ? "Selected" : "Review purchase"}</Button></div>
                      </div>
                    </article>
                  );
                }) : <div className="border-y border-border py-8"><p className="font-bold text-foreground" role="status">Purchasing is temporarily unavailable.</p><p className="mt-2 text-sm text-muted-foreground">New credit bundles are not available yet. Your existing access, balances and accepted purchases remain available.</p></div>}
              </div>
              <div className="mt-8 border-y border-border py-6 text-sm leading-6 text-muted-foreground"><h3 className="font-semibold text-foreground">About analysis units</h3><p className="mt-2">Analysis units are software usage allowances, not money or stored value. They cannot be withdrawn, resold, or transferred. Job service credits are a separate prepaid allowance for employer search and confirmed automatic applications; Premium does not waive those charges.</p><Link href="/pricing" className="mt-3 inline-flex items-center gap-1 font-bold text-primary hover:underline">Read full pricing and usage details <ExternalLink size={13} /></Link></div>
            </section>

            <section aria-labelledby="checkout-heading">
              <div><p className="data-label">Purchase review</p><h2 id="checkout-heading" className="font-display mt-2 text-2xl font-normal text-foreground">Order summary</h2><p className="mt-2 text-sm text-muted-foreground">Review the amount, seller, delivery, and renewal terms.</p></div>
              <div className="surface-panel mt-6 p-5 sm:p-7">
                {selectedProduct ? <div>
                  <div className={`border-l-2 pl-4 ${checkoutEnabled ? "border-primary" : "border-primary/30"}`}><div className="flex items-start gap-3">{checkoutEnabled ? <Shield className="mt-0.5 shrink-0 text-primary" size={18} /> : <Clock3 className="mt-0.5 shrink-0 text-primary" size={18} />}<div><h3 className="font-semibold text-foreground">{checkoutEnabled ? "Secure checkout available" : "Checkout is not available yet"}</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">{checkoutEnabled ? "Confirm India billing, then continue to Razorpay hosted checkout." : "The product remains visible while purchases are paused."}</p></div></div></div>
                  <div className="mt-6 grid grid-cols-[1fr_auto] gap-4 border-y border-border py-5"><div><p className="data-label">Product</p><p className="mt-2 font-semibold text-foreground">{selectedProduct.name}</p></div><div className="text-right"><p className="data-label">Due today</p><p className="mt-2 text-2xl font-semibold text-primary">{formatPrice(selectedProduct.amount_minor, selectedProduct.currency)}</p></div></div>
                  <dl className="divide-y divide-border text-sm">{[["Purchase type", "One-time payment"], ["Automatic renewal", "No"], ["Trial", "None"], ["Billing country", "India"], ["Seller", "HireWiz, operated by SAVALIYA HARSHIL YOGESHBHAI"], ["Delivery", `${selectedProduct.analysis_units} analysis units and ${selectedProduct.job_service_credits} service credits after verified payment`]].map(([term, value]) => <div key={term} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] gap-4 py-3"><dt className="text-muted-foreground">{term}</dt><dd className="text-right font-semibold text-foreground">{value}</dd></div>)}</dl>
                  <p className="border-y border-border py-4 text-xs leading-5 text-muted-foreground">The server locks this total before checkout. Compare the same amount in the hosted payment window before authorizing payment.</p>
                  <label className="mt-5 flex cursor-pointer items-start gap-3 text-sm leading-6 text-muted-foreground"><input type="checkbox" checked={indiaBillingConfirmed} onChange={(event) => setIndiaBillingConfirmed(event.target.checked)} disabled={currentTier === null || !checkoutEnabled} className="mt-1 h-4 w-4 shrink-0 accent-primary" /><span>I confirm my billing country is India and this INR purchase is for domestic use.</span></label>
                  <div className="mt-5">
                    {currentTier === null ? <div className="flex items-start gap-3 border-y border-border py-4 text-sm text-muted-foreground"><Lock size={17} className="mt-0.5 shrink-0" />Checkout waits for account status.</div> : checkoutEnabled && selectedProduct.enabled_for_purchase ? <Button type="button" onClick={handleCheckout} disabled={!purchaseAllowed || checkoutBusy} className="w-full">{checkoutBusy ? <><RefreshCw size={17} className="animate-spin" />{phase === "confirming" ? "Waiting for verified confirmation" : "Opening hosted checkout"}</> : <><CreditCard size={17} />Pay with Razorpay | {formatPrice(selectedProduct.amount_minor, selectedProduct.currency)}</>}</Button> : <Button type="button" variant="secondary" disabled className="w-full">Checkout unavailable</Button>}
                  </div>
                </div> : <div className="py-8 text-center"><FileText className="mx-auto text-muted-foreground" size={28} /><p className="mt-3 font-bold text-foreground">New purchases are temporarily unavailable.</p></div>}

                {currentOrder && phase !== "confirmed" ? <div className="mt-6 border-t border-border pt-5 text-xs text-muted-foreground"><p className="break-all">Order reference: {currentOrder.order_id}</p>{currentOrder.payment_reference ? <p className="break-all">Payment reference: {currentOrder.payment_reference}</p> : null}<Button type="button" variant="secondary" size="sm" onClick={checkCurrentOrder} disabled={phase === "confirming"} className="mt-3"><RefreshCw size={13} className={phase === "confirming" ? "animate-spin" : ""} />Check payment status</Button></div> : null}
              </div>

              <div className="mt-6 border-y border-border py-5 text-sm leading-6 text-muted-foreground"><div className="flex items-start gap-3"><Shield size={18} className="mt-0.5 shrink-0 text-primary" /><p>Card, CVV, UPI PIN, bank-login, and other payment credentials are entered only in Razorpay hosted checkout.</p></div><div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 border-t border-border pt-4 text-xs font-bold">{[["/terms", "Terms"], ["/privacy", "Privacy"], ["/refund", "Refunds"], ["/digital-delivery", "Digital delivery"], ["/contact", "Billing support"]].map(([href, label]) => <Link key={href} href={href} className="text-primary hover:underline">{label}</Link>)}</div></div>
            </section>
          </div>
        )}

        <section className="border-t border-border pt-8" aria-labelledby="usage-history-heading">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="data-label">Durable ledger</p><h2 id="usage-history-heading" className="font-display mt-2 flex items-center gap-2 text-2xl font-normal text-foreground"><History size={20} className="text-primary" /> Usage history</h2><p className="mt-2 text-sm text-muted-foreground">Reservations, completed operations, Premium waivers, and automatic releases.</p></div><p className="text-sm font-bold text-foreground">Current balance: <span className="text-primary">{analysisUnits}</span></p></div>
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
