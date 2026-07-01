"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { PayPalScriptProvider, PayPalButtons } from "@paypal/react-paypal-js";
import { apiPostJson } from "../../lib/api";

const PAYPAL_CLIENT_ID = process.env.NEXT_PUBLIC_PAYPAL_CLIENT_ID || "test"; // fallback to sandbox test client

export default function BillingPage() {
  const router = useRouter();
  const [selectedPlan, setSelectedPlan] = useState<"subscription" | "topup">("subscription");
  const [paymentSuccess, setPaymentSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const planDetails = {
    subscription: {
      title: "Premium Plan",
      price: "$19.00",
      period: "monthly",
      features: [
        "Unlimited resume parses (PDF & DOCX)",
        "Unlimited job match reports",
        "Unlimited Ask AI (RAG) assistant queries",
        "Full market skill gap analyses",
        "Custom learning recommendations & project paths",
      ],
    },
    topup: {
      title: "10 Operations Credits",
      price: "$10.00",
      period: "one-time",
      features: [
        "10 AI Operation credits added instantly",
        "Credits do not expire",
        "Use on parses, matches, and optimizations",
        "Perfect for casual job hunters",
      ],
    },
  };

  async function createOrder() {
    setError(null);
    try {
      const payload = {
        type: selectedPlan,
        currency: "USD",
        credits: selectedPlan === "topup" ? 10 : 0,
      };
      // Trigger backend PayPal order generation
      const res = await apiPostJson<{ order_id: string }>("/billing/paypal/create-order", payload);
      return res.order_id;
    } catch (err: any) {
      setError(err.message || "Failed to initialize PayPal order.");
      throw err;
    }
  }

  async function onApprove(data: any) {
    setLoading(true);
    setError(null);
    try {
      const payload = {
        order_id: data.orderID,
        type: selectedPlan,
        credits: selectedPlan === "topup" ? 10 : 0,
      };
      // Trigger backend capture and DB updates
      await apiPostJson("/billing/paypal/capture-order", payload);
      setPaymentSuccess(true);
      setTimeout(() => {
        router.push("/dashboard");
      }, 3000);
    } catch (err: any) {
      setError(err.message || "Payment capture failed. Contact support.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell max-w-5xl mx-auto py-10 px-4 space-y-8">
      <section className="product-hero text-left p-7 md:p-10">
        <div className="label-kicker flex items-center gap-3">
          <span className="pulse-dot bg-blue-500" /> Commercial Account
        </div>
        <h1 className="text-5xl md:text-7xl font-black leading-[0.88] mt-4 text-slate-950">
          Upgrade your Career Command Center.
        </h1>
        <p className="text-slate-600 mt-4 max-w-2xl leading-relaxed">
          Select a commercial tier to unlock advanced AI parsing, job matches, and market trend tracking.
        </p>
      </section>

      {paymentSuccess && (
        <div className="panel kinetic-border p-8 text-center bg-green-50 border-green-200">
          <div className="text-4xl">🎉</div>
          <h2 className="text-2xl font-black text-green-950 mt-4">Payment Completed Successfully!</h2>
          <p className="text-green-700 mt-2">Your tier and credit privileges are active. Redirecting to workspace...</p>
        </div>
      )}

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {!paymentSuccess && (
        <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_0.8fr] gap-6">
          {/* Plan Selection Cards */}
          <div className="space-y-4">
            <h3 className="text-lg font-black text-slate-950">1. Select Package</h3>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Subscription Option */}
              <div
                onClick={() => setSelectedPlan("subscription")}
                className={`panel p-6 cursor-pointer transition tilt-lift flex flex-col justify-between min-h-[320px] ${
                  selectedPlan === "subscription"
                    ? "kinetic-border ring-2 ring-neutral-950 bg-white"
                    : "border-slate-200 bg-slate-50/50 hover:bg-white"
                }`}
              >
                <div>
                  <div className="label-kicker text-blue-500">Subscription</div>
                  <h4 className="text-2xl font-black text-slate-900 mt-2">{planDetails.subscription.title}</h4>
                  <div className="mt-4 flex items-baseline">
                    <span className="text-4xl font-black text-slate-950">{planDetails.subscription.price}</span>
                    <span className="text-sm text-slate-500 ml-1">/ {planDetails.subscription.period}</span>
                  </div>
                  <ul className="mt-6 space-y-2 text-xs font-semibold text-slate-600">
                    {planDetails.subscription.features.map((f) => (
                      <li key={f}>✓ {f}</li>
                    ))}
                  </ul>
                </div>
                <div className="mt-6 text-sm font-black text-blue-600">Best for active search</div>
              </div>

              {/* Credits Top-up Option */}
              <div
                onClick={() => setSelectedPlan("topup")}
                className={`panel p-6 cursor-pointer transition tilt-lift flex flex-col justify-between min-h-[320px] ${
                  selectedPlan === "topup"
                    ? "kinetic-border ring-2 ring-neutral-950 bg-white"
                    : "border-slate-200 bg-slate-50/50 hover:bg-white"
                }`}
              >
                <div>
                  <div className="label-kicker text-slate-500">Top-Up Packs</div>
                  <h4 className="text-2xl font-black text-slate-900 mt-2">{planDetails.topup.title}</h4>
                  <div className="mt-4 flex items-baseline">
                    <span className="text-4xl font-black text-slate-950">{planDetails.topup.price}</span>
                    <span className="text-sm text-slate-500 ml-1">/ {planDetails.topup.period}</span>
                  </div>
                  <ul className="mt-6 space-y-2 text-xs font-semibold text-slate-600">
                    {planDetails.topup.features.map((f) => (
                      <li key={f}>✓ {f}</li>
                    ))}
                  </ul>
                </div>
                <div className="mt-6 text-sm font-black text-slate-600">Pay as you go</div>
              </div>
            </div>
          </div>

          {/* PayPal Payment Area */}
          <div className="panel kinetic-border p-6 flex flex-col justify-center space-y-6 bg-white/70 backdrop-blur-xl">
            <div>
              <h3 className="text-lg font-black text-slate-950">2. Complete Checkout</h3>
              <p className="text-xs text-slate-500 mt-1">
                Supports PayPal account, Debit/Credit Card (Visa & Mastercard), and Google Pay (GPay).
              </p>
            </div>

            <div className="premium-card p-4 bg-slate-50 flex justify-between items-center">
              <div>
                <div className="text-xs text-slate-400 font-bold uppercase tracking-wider">Total Amount</div>
                <div className="text-lg font-black text-slate-900 mt-1">
                  {selectedPlan === "subscription" ? planDetails.subscription.title : planDetails.topup.title}
                </div>
              </div>
              <div className="text-3xl font-black text-slate-950">
                {selectedPlan === "subscription" ? planDetails.subscription.price : planDetails.topup.price}
              </div>
            </div>

            {loading && <div className="text-center py-4 text-sm text-slate-500">Capturing checkout session details...</div>}

            {!loading && (
              <div className="relative z-10">
                <PayPalScriptProvider
                  options={{
                    clientId: PAYPAL_CLIENT_ID,
                    currency: "USD",
                    intent: "capture",
                    components: "buttons",
                  }}
                >
                  <PayPalButtons
                    style={{
                      layout: "vertical",
                      shape: "pill",
                      color: "gold",
                    }}
                    forceReRender={[selectedPlan]}
                    createOrder={createOrder}
                    onApprove={onApprove}
                    onError={(err) => {
                      console.error("PayPal buttons error:", err);
                      setError("Unable to render payment options. Check environment credentials.");
                    }}
                  />
                </PayPalScriptProvider>
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
