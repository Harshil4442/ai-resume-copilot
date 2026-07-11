"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  CircleGauge,
  Clock3,
  Crown,
  ExternalLink,
  FileText,
  Lock,
  RefreshCw,
  Shield,
  ShoppingBag,
} from "lucide-react";
import { apiGet, apiPostJson } from "../../lib/api";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";

const RAZORPAY_CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";
const SUPPORTED_SKUS = ["premium_30d"] as const;
const CONFIRMATION_ATTEMPTS = 24;
const CONFIRMATION_INTERVAL_MS = 2500;

type SupportedSku = (typeof SUPPORTED_SKUS)[number];
type CheckoutPhase = "idle" | "opening" | "confirming" | "pending" | "confirmed";

interface BillingProduct {
  sku: SupportedSku;
  name: string;
  description: string;
  amount_minor: number;
  amount_display: string;
  currency: "INR";
  billing_type: "one_time";
  duration_days: number;
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
  sku: SupportedSku;
  status: "pending" | "paid" | "failed" | "refunded";
  fulfilled: boolean;
  amount_minor: number;
  currency: "INR";
  created_at: string;
  paid_at: string | null;
  refunded_at: string | null;
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

function isSupportedSku(value: string): value is SupportedSku {
  return SUPPORTED_SKUS.includes(value as SupportedSku);
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
    `${product.duration_days} days of Premium access`,
    "Unlimited AI-assisted operations during the access period",
    "One-time payment with no automatic renewal",
    "No free trial or stored payment mandate",
  ];
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export default function BillingPage() {
  const mountedRef = useRef(true);
  const pollRunRef = useRef(0);
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [selectedSku, setSelectedSku] = useState<SupportedSku | null>(null);
  const [currentTier, setCurrentTier] = useState<string | null>(null);
  const [analysisUnits, setAnalysisUnits] = useState(0);
  const [indiaBillingConfirmed, setIndiaBillingConfirmed] = useState(false);
  const [phase, setPhase] = useState<CheckoutPhase>("idle");
  const [currentOrder, setCurrentOrder] = useState<BillingOrderStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshProfile = useCallback(async () => {
    const profile = await apiGet<{ tier?: string; ai_credits?: number }>("/auth/profile");
    if (!mountedRef.current) return;
    setCurrentTier(profile.tier || "free");
    setAnalysisUnits(profile.ai_credits ?? 0);
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    apiGet<BillingCatalog>("/public/billing/catalog")
      .then((response) => {
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

    return () => {
      mountedRef.current = false;
      pollRunRef.current += 1;
    };
  }, [refreshProfile]);

  const visibleProducts = useMemo(() => {
    if (!catalog) return [];
    return catalog.products.filter((product) => product.catalog_visible);
  }, [catalog]);

  const selectedProduct = useMemo(
    () => visibleProducts.find((product) => product.sku === selectedSku) ?? null,
    [selectedSku, visibleProducts],
  );

  const checkoutEnabled = Boolean(
    catalog?.checkout_enabled && catalog.provider === "razorpay",
  );
  const purchaseAllowed = Boolean(
    checkoutEnabled &&
      selectedProduct?.enabled_for_purchase &&
      currentTier === "free" &&
      indiaBillingConfirmed,
  );
  const checkoutBusy = phase === "opening" || phase === "confirming";

  const markConfirmed = useCallback(
    async (order: BillingOrderStatus) => {
      if (!mountedRef.current) return;
      setCurrentOrder(order);
      setPhase("confirmed");
      setError(null);
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
    apiGet<BillingOrderStatus>("/billing/recent-order")
      .then(async (order) => {
        if (cancelled || !mountedRef.current) return;
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
            setPhase("pending");
            setError(
              "Checkout was closed. No payment is assumed. If you completed a payment, check the server status before trying again.",
            );
          },
        },
        retry: { enabled: true },
        theme: { color: "#2563eb" },
      });

      checkout.on("payment.failed", (response) => {
        setPhase("pending");
        setError(
          response.error?.description ||
            "The payment attempt was not completed. Your HireWiz access was not changed.",
        );
      });
      checkout.open();
    } catch (checkoutError: unknown) {
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
  }, [currentOrder?.order_id, pollOrder]);

  if (phase === "confirmed" && currentOrder) {
    return (
      <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-10">
        <FadeIn>
          <GlassCard
            className="p-8 md:p-12 text-center border-emerald-800 bg-emerald-950/30"
            hoverEffect={false}
          >
            <div className="w-16 h-16 mx-auto rounded-full bg-emerald-900/60 text-emerald-300 flex items-center justify-center mb-6">
              <CheckCircle2 size={32} />
            </div>
            <h1 className="text-3xl font-black text-white tracking-tight">Payment confirmed</h1>
            <p className="mt-3 text-slate-300 max-w-xl mx-auto">
              The server received verified payment confirmation and activated the purchased HireWiz access.
            </p>
            <p className="mt-4 text-xs text-slate-400 break-all">
              Order reference: {currentOrder.order_id}
            </p>
            {currentOrder.payment_reference ? (
              <p className="mt-1 text-xs text-slate-400 break-all">
                Payment reference: {currentOrder.payment_reference}
              </p>
            ) : null}
            <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3">
              <Link
                href="/dashboard"
                className="inline-flex items-center justify-center rounded-xl bg-primary px-5 py-3 text-sm font-bold text-white hover:bg-primary/90"
              >
                Continue to dashboard
              </Link>
              <Link
                href="/contact"
                className="inline-flex items-center justify-center rounded-xl border border-slate-700 px-5 py-3 text-sm font-bold text-slate-200 hover:bg-slate-800"
              >
                Billing support
              </Link>
            </div>
          </GlassCard>
        </FadeIn>
      </main>
    );
  }

  return (
    <main className="w-full max-w-[76rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader
        badge="Account billing"
        title="Manage your HireWiz access."
        subtitle="India-only pricing in INR. Purchases are one-time, do not auto-renew, and are delivered digitally after verified payment confirmation."
      />

      <GlassCard
        className="p-6 md:p-8 bg-gradient-to-r from-slate-900 to-blue-950 border-slate-800 text-white flex flex-col md:flex-row items-start md:items-center justify-between gap-6"
        hoverEffect={false}
      >
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-slate-800 border border-slate-700 flex items-center justify-center">
            {currentTier === "premium" ? (
              <Crown className="text-amber-400" size={24} />
            ) : (
              <CircleGauge className="text-blue-400" size={24} aria-hidden="true" />
            )}
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400">Current access</div>
            <h2 className="text-xl font-black tracking-tight text-white">
              {currentTier === "premium"
                ? "Premium active"
                : currentTier === "free"
                  ? "Free access"
                  : "Checking account status…"}
            </h2>
          </div>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-950/40 px-5 py-3">
          <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400">Analysis units</div>
          <div className="mt-1 text-xl font-black text-white">
            {currentTier === "premium"
              ? "Unlimited while active"
              : currentTier === "free"
                ? analysisUnits
                : "—"}
          </div>
        </div>
      </GlassCard>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-3 text-sm text-rose-200 bg-rose-950/40 border border-rose-800 rounded-xl px-4 py-3"
        >
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {catalogLoading ? (
        <GlassCard className="p-12 text-center" hoverEffect={false}>
          <RefreshCw className="mx-auto animate-spin text-primary" size={24} />
          <p className="mt-3 text-sm font-semibold text-slate-300">Loading server-owned billing information…</p>
        </GlassCard>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[1.15fr_0.85fr] gap-8 items-start">
          <section className="space-y-5" aria-labelledby="products-heading">
            <div>
              <h2 id="products-heading" className="text-xl font-black text-white tracking-tight">
                Available products
              </h2>
              <p className="mt-1 text-sm text-slate-400">
                Prices below come from the server catalog. Select a product to review the complete order summary.
              </p>
            </div>

            {visibleProducts.length === 0 ? (
              <GlassCard className="p-8" hoverEffect={false}>
                <p className="font-bold text-white">No paid product is currently available.</p>
                <p className="mt-2 text-sm text-slate-400">
                  No checkout will be loaded. View the public pricing information or contact billing support.
                </p>
              </GlassCard>
            ) : (
              <div className="grid grid-cols-1 gap-4">
                {visibleProducts.map((product) => {
                  const selected = product.sku === selectedSku;
                  return (
                    <GlassCard
                      key={product.sku}
                      className={`p-6 md:p-8 transition-colors ${
                        selected ? "border-primary ring-2 ring-primary/40" : "border-slate-800"
                      }`}
                      hoverEffect={false}
                    >
                      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-5">
                        <div className="min-w-0">
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl bg-blue-950 text-blue-300 flex items-center justify-center">
                              <Crown size={20} />
                            </div>
                            <div>
                              <h3 className="text-xl font-black text-white">{product.name}</h3>
                              <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                One-time purchase · no automatic renewal
                              </p>
                            </div>
                          </div>
                          <p className="mt-4 text-sm text-slate-300">{product.description}</p>
                          <ul className="mt-5 space-y-2">
                            {productFacts(product).map((fact) => (
                              <li key={fact} className="flex items-start gap-2 text-sm text-slate-300">
                                <CheckCircle2 size={15} className="text-emerald-400 mt-0.5 shrink-0" />
                                <span>{fact}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                        <div className="sm:text-right shrink-0">
                          <div className="text-3xl font-black text-white">
                            {formatPrice(product.amount_minor, product.currency)}
                          </div>
                          <div className="text-xs font-bold text-slate-400 mt-1">INR total · one-time</div>
                          <button
                            type="button"
                            onClick={() => setSelectedSku(product.sku)}
                            disabled={currentTier !== "free"}
                            className="mt-4 w-full sm:w-auto rounded-xl border border-primary/60 px-4 py-2.5 text-sm font-bold text-blue-200 hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {currentTier === "premium"
                              ? "Premium already active"
                              : currentTier === null
                                ? "Checking account status…"
                                : selected
                                  ? "Selected"
                                  : "Review this purchase"}
                          </button>
                        </div>
                      </div>
                    </GlassCard>
                  );
                })}
              </div>
            )}

            <GlassCard className="p-6 text-sm text-slate-300" hoverEffect={false}>
              <h3 className="font-black text-white">About analysis units</h3>
              <p className="mt-2 leading-relaxed">
                Analysis units are usage allowances for specified HireWiz software functions. They are not money, a
                wallet, or stored value; they cannot be withdrawn, resold, or transferred. Standalone unit packs are
                not offered in this checkout.
              </p>
              <Link href="/pricing" className="mt-3 inline-flex items-center gap-1 text-primary font-bold hover:underline">
                Read full pricing and usage details <ExternalLink size={13} />
              </Link>
            </GlassCard>
          </section>

          <section className="space-y-5" aria-labelledby="checkout-heading">
            <div>
              <h2 id="checkout-heading" className="text-xl font-black text-white tracking-tight">
                Order summary
              </h2>
              <p className="mt-1 text-sm text-slate-400">Review the seller, amount, delivery and renewal terms.</p>
            </div>

            {!checkoutEnabled && (
              <GlassCard className="p-6 border-amber-800 bg-amber-950/20" hoverEffect={false}>
                <div className="flex items-start gap-3">
                  <Clock3 className="text-amber-300 mt-0.5 shrink-0" size={20} />
                  <div>
                    <h3 className="font-black text-white">Online payments are under review</h3>
                    <p className="mt-2 text-sm text-slate-300 leading-relaxed">
                      Purchases are not currently available. No checkout provider is advertised or loaded while
                      payment activation is pending. You can continue using the free product features.
                    </p>
                  </div>
                </div>
              </GlassCard>
            )}

            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              {selectedProduct ? (
                <div className="space-y-5">
                  <div className="flex items-start justify-between gap-4 pb-5 border-b border-slate-800">
                    <div>
                      <div className="text-xs font-bold uppercase tracking-wider text-slate-400">Product</div>
                      <div className="mt-1 font-black text-white">{selectedProduct.name}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs font-bold uppercase tracking-wider text-slate-400">Total due today</div>
                      <div className="mt-1 text-2xl font-black text-primary">
                        {formatPrice(selectedProduct.amount_minor, selectedProduct.currency)}
                      </div>
                      <div className="text-xs text-slate-400">INR</div>
                    </div>
                  </div>

                  <dl className="space-y-3 text-sm">
                    <div className="flex justify-between gap-4">
                      <dt className="text-slate-400">Purchase type</dt>
                      <dd className="font-semibold text-white text-right">One-time payment</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-slate-400">Automatic renewal</dt>
                      <dd className="font-semibold text-white text-right">No</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-slate-400">Trial</dt>
                      <dd className="font-semibold text-white text-right">None</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-slate-400">Billing country</dt>
                      <dd className="font-semibold text-white text-right">India</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-slate-400">Seller</dt>
                      <dd className="font-semibold text-white text-right max-w-[16rem]">
                        HireWiz, operated by SAVALIYA HARSHIL YOGESHBHAI
                      </dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-slate-400">Delivery</dt>
                      <dd className="font-semibold text-white text-right max-w-[16rem]">
                        Digital access in your HireWiz account after verified payment confirmation
                      </dd>
                    </div>
                  </dl>

                  <div className="rounded-xl border border-slate-800 bg-slate-950/30 p-4 text-xs text-slate-300 leading-relaxed">
                    The total shown is locked by the server for this product. No separate HireWiz fee or tax is added,
                    and no optional paid add-on is selected. Check the same final amount in the hosted checkout before
                    authorizing payment.
                  </div>

                  <label className="flex items-start gap-3 rounded-xl border border-slate-700 bg-slate-900/40 p-4 text-sm text-slate-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={indiaBillingConfirmed}
                      onChange={(event) => setIndiaBillingConfirmed(event.target.checked)}
                      disabled={currentTier !== "free" || !checkoutEnabled}
                      className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
                    />
                    <span>
                      I confirm that my billing country is India and I am purchasing this INR pass for domestic use.
                    </span>
                  </label>

                  {currentTier === "premium" ? (
                    <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-4 text-sm text-slate-300 flex items-start gap-3">
                      <Lock size={17} className="mt-0.5 shrink-0" />
                      Additional purchases are disabled while your Premium access is active.
                    </div>
                  ) : currentTier === null ? (
                    <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-4 text-sm text-slate-300 flex items-start gap-3">
                      <Lock size={17} className="mt-0.5 shrink-0" />
                      Checkout remains disabled until your current account status is confirmed.
                    </div>
                  ) : checkoutEnabled && selectedProduct.enabled_for_purchase ? (
                    <button
                      type="button"
                      onClick={handleCheckout}
                      disabled={!purchaseAllowed || checkoutBusy}
                      className="w-full rounded-xl bg-primary hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 text-white font-bold py-4 px-5 transition flex items-center justify-center gap-2"
                    >
                      {checkoutBusy ? (
                        <>
                          <RefreshCw size={17} className="animate-spin" />
                          {phase === "confirming" ? "Waiting for verified confirmation…" : "Opening hosted checkout…"}
                        </>
                      ) : (
                        <>
                          <ShoppingBag size={17} />
                          Continue to secure checkout · {formatPrice(selectedProduct.amount_minor, selectedProduct.currency)}
                        </>
                      )}
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled
                      className="w-full rounded-xl border border-slate-700 bg-slate-900 text-slate-400 font-bold py-4 px-5 cursor-not-allowed"
                    >
                      Purchase unavailable while payments are under review
                    </button>
                  )}
                </div>
              ) : (
                <div className="py-8 text-center">
                  <FileText className="mx-auto text-slate-500" size={28} />
                  <p className="mt-3 font-bold text-white">Select a product to review its order summary.</p>
                  <p className="mt-1 text-sm text-slate-400">Nothing is preselected and no checkout has been started.</p>
                </div>
              )}

              {currentOrder && phase !== "confirmed" && (
                <div className="mt-6 pt-5 border-t border-slate-800">
                  <div className="text-xs text-slate-400 break-all">Order reference: {currentOrder.order_id}</div>
                  {currentOrder.payment_reference ? (
                    <div className="mt-1 text-xs text-slate-400 break-all">
                      Payment reference: {currentOrder.payment_reference}
                    </div>
                  ) : null}
                  <button
                    type="button"
                    onClick={checkCurrentOrder}
                    disabled={phase === "confirming"}
                    className="mt-3 inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-200 hover:bg-slate-800 disabled:opacity-60"
                  >
                    <RefreshCw size={13} className={phase === "confirming" ? "animate-spin" : ""} />
                    Check payment status
                  </button>
                </div>
              )}
            </GlassCard>

            <GlassCard className="p-6 text-sm text-slate-300 space-y-4" hoverEffect={false}>
              <div className="flex items-start gap-3">
                <Shield size={18} className="text-primary mt-0.5 shrink-0" />
                <p>
                  When checkout is available, card, UPI, bank and other payment credentials are entered only in the
                  hosted payment page. HireWiz does not collect raw card details, CVV, UPI PINs or bank credentials.
                </p>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-2 pt-4 border-t border-slate-800 text-xs font-bold">
                <Link href="/terms" className="text-primary hover:underline">Terms</Link>
                <Link href="/privacy" className="text-primary hover:underline">Privacy</Link>
                <Link href="/refund" className="text-primary hover:underline">Refund &amp; cancellation</Link>
                <Link href="/digital-delivery" className="text-primary hover:underline">Digital delivery</Link>
                <Link href="/contact" className="text-primary hover:underline">Billing support</Link>
              </div>
            </GlassCard>
          </section>
        </div>
      )}
    </main>
  );
}
