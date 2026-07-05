"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Script from "next/script";
import { PayPalScriptProvider, PayPalButtons } from "@paypal/react-paypal-js";
import { apiGet, apiPostJson } from "../../lib/api";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { CreditCard, Zap, CheckCircle2, Shield, Globe, MapPin, AlertCircle, Crown, Lock } from "lucide-react";

const PAYPAL_CLIENT_ID = process.env.NEXT_PUBLIC_PAYPAL_CLIENT_ID || "test";
const RAZORPAY_KEY_ID = process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID || "rzp_test_mock";

export default function BillingPage() {
  const router = useRouter();
  const [region, setRegion] = useState<"global" | "india">("global");
  const [selectedPlan, setSelectedPlan] = useState<"subscription" | "topup">("subscription");
  const [paymentSuccess, setPaymentSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentTier, setCurrentTier] = useState<string>("free");
  const [creditBalance, setCreditBalance] = useState<number>(0);

  useEffect(() => {
    let mounted = true;
    apiGet<any>("/auth/profile")
      .then((data) => {
        if (mounted) {
          setCurrentTier(data.tier || "free");
          setCreditBalance(data.ai_credits ?? 0);
        }
      })
      .catch((err) => {
        console.error("Failed to load profile for billing page:", err);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const planDetails = {
    subscription: {
      title: "Premium Plan",
      price: region === "india" ? "₹999" : "$15",
      period: "monthly",
      features: [
        "Unlimited job match reports",
        "Unlimited Ask AI assistant queries",
        "Full market skill gap analyses",
        "Custom learning paths & projects",
      ],
    },
    topup: {
      title: "Credits Pack",
      price: region === "india" ? "₹199" : "$5",
      period: "one-time",
      features: [
        "10 AI Operation credits instantly",
        "Credits never expire",
        "Use on matches, chats, and learning",
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
      await apiPostJson("/billing/paypal/capture-order", payload);
      setPaymentSuccess(true);
      window.dispatchEvent(new Event("refresh_credits"));
      setTimeout(() => {
        router.push("/dashboard");
      }, 3000);
    } catch (err: any) {
      setError(err.message || "Payment capture failed. Contact support.");
    } finally {
      setLoading(false);
    }
  }

  async function handleRazorpayCheckout() {
    setLoading(true);
    setError(null);
    try {
      const payload = {
        type: selectedPlan,
        credits: selectedPlan === "topup" ? 10 : 0,
      };
      const res = await apiPostJson<{ order_id: string, amount: number }>("/billing/razorpay/create-order", payload);

      const options = {
        key: RAZORPAY_KEY_ID,
        amount: res.amount,
        currency: "INR",
        name: "AI Resume CoPilot",
        description: planDetails[selectedPlan].title,
        order_id: res.order_id,
        config: {
          display: {
            blocks: {
              upi: { name: "Pay via UPI", instruments: [{ method: "upi" }] },
              other: { name: "Other Modes", instruments: [{ method: "card" }, { method: "netbanking" }, { method: "wallet" }] }
            },
            sequence: ["block.upi", "block.other"],
            preferences: { show_default_blocks: false }
          }
        },
        handler: async function (response: any) {
          try {
            await apiPostJson("/billing/razorpay/verify-payment", {
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
              type: selectedPlan,
              credits: selectedPlan === "topup" ? 10 : 0,
            });
            setPaymentSuccess(true);
            window.dispatchEvent(new Event("refresh_credits"));
            setTimeout(() => {
              router.push("/dashboard");
            }, 3000);
          } catch (verifyErr: any) {
            setError(verifyErr.message || "Payment verification failed.");
          }
        },
        theme: { color: "#0f172a" },
      };

      const rzp = new (window as any).Razorpay(options);
      rzp.on("payment.failed", function (response: any) {
        setError(response.error.description || "Razorpay payment failed.");
      });
      rzp.open();
    } catch (err: any) {
      setError(err.message || "Failed to initialize Razorpay order.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />
      
      <PageHeader 
        badge="Commercial Account"
        title="Upgrade your Career Command Center."
        subtitle="Select a commercial tier to unlock advanced AI parsing, job matches, and market trend tracking."
      />

      {/* Current Status Bar */}
      <GlassCard className="p-6 md:p-8 bg-gradient-to-r from-slate-900 to-blue-950 border-slate-800 text-white flex flex-col md:flex-row items-start md:items-center justify-between gap-6 overflow-hidden relative" hoverEffect={false}>
        <div className="absolute top-0 right-0 w-64 h-64 bg-primary/20 blur-3xl rounded-full -mr-20 -mt-20 pointer-events-none"></div>
        
        <div className="relative z-10 flex items-center gap-5">
          <div className="w-14 h-14 rounded-2xl bg-white/10 border border-white/20 flex items-center justify-center backdrop-blur-sm">
            {currentTier === "premium" ? <Crown className="text-amber-400" size={28} /> : <Zap className="text-blue-400" size={28} />}
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400 mb-1">Current Plan</div>
            <h2 className="text-2xl font-black tracking-tight text-white flex items-center gap-2">
              {currentTier === "premium" ? "Premium Access Active" : "Free Tier Active"}
            </h2>
          </div>
        </div>
        
        <div className="relative z-10 bg-black/30 border border-white/10 px-6 py-4 rounded-2xl flex items-center gap-6 w-full md:w-auto">
          <div>
            <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1">Available Credits</div>
            <div className="text-3xl font-black text-white">
              {currentTier === "premium" ? "∞" : creditBalance}
            </div>
          </div>
          <div className="w-px h-10 bg-white/10"></div>
          <div className="text-xs text-slate-400 font-bold max-w-[120px] leading-snug">
            {currentTier === "premium" ? "Unlimited Operations" : "Pay-As-You-Go Operations"}
          </div>
        </div>
      </GlassCard>

      {paymentSuccess && (
        <FadeIn>
          <GlassCard className="p-10 text-center bg-emerald-50/50 border-emerald-200" hoverEffect={false}>
            <div className="w-20 h-20 mx-auto rounded-full bg-emerald-100 flex items-center justify-center text-emerald-600 mb-6">
              <CheckCircle2 size={40} />
            </div>
            <h2 className="text-3xl font-black text-slate-900 tracking-tighter mb-2">Payment Completed!</h2>
            <p className="text-slate-600 font-medium max-w-md mx-auto">Your tier and credit privileges have been successfully activated. We are redirecting you to your dashboard...</p>
          </GlassCard>
        </FadeIn>
      )}

      {error && (
        <FadeIn>
          <div className="flex items-center gap-2 text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-4 py-3 shadow-sm max-w-4xl mx-auto">
            <AlertCircle size={16} /> {error}
          </div>
        </FadeIn>
      )}

      {!paymentSuccess && (
        <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-8">
          <div className="space-y-8">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div>
                <h3 className="text-xl font-black text-slate-900 tracking-tighter">1. Select Package</h3>
                <p className="text-xs font-bold text-slate-500 uppercase tracking-wider mt-1">Pricing adjusts automatically to your location</p>
              </div>
              <div className="flex bg-slate-100 p-1 rounded-xl shadow-inner border border-slate-200">
                <button
                  onClick={() => setRegion("global")}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-xs font-bold transition-all ${
                    region === "global" ? "bg-white text-slate-900 shadow-sm border border-slate-200/50" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  <Globe size={14} /> Global (USD)
                </button>
                <button
                  onClick={() => setRegion("india")}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-xs font-bold transition-all ${
                    region === "india" ? "bg-white text-slate-900 shadow-sm border border-slate-200/50" : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  <MapPin size={14} /> India (INR)
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Premium Plan Card */}
              <GlassCard 
                className={`p-6 md:p-8 flex flex-col justify-between h-[380px] transition-all cursor-pointer relative overflow-hidden ${
                  currentTier === "premium" 
                    ? "opacity-60 grayscale cursor-not-allowed border-slate-200" 
                    : selectedPlan === "subscription"
                      ? "ring-2 ring-primary border-primary shadow-xl scale-[1.02] bg-blue-50/20"
                      : "hover:border-primary/50 hover:shadow-lg hover:-translate-y-1"
                }`}
                hoverEffect={false}
                onClick={() => currentTier !== "premium" && setSelectedPlan("subscription")}
              >
                {selectedPlan === "subscription" && currentTier !== "premium" && (
                  <div className="absolute top-0 right-0 bg-primary text-white text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded-bl-xl">Selected</div>
                )}
                
                <div>
                  <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center text-blue-600 mb-4">
                    <Crown size={20} />
                  </div>
                  <h4 className="text-xl font-black text-slate-900">{planDetails.subscription.title}</h4>
                  <div className="mt-3 flex items-baseline gap-1">
                    <span className="text-4xl font-black text-slate-900 tracking-tighter">{planDetails.subscription.price}</span>
                    <span className="text-sm font-bold text-slate-400">/ {planDetails.subscription.period}</span>
                  </div>
                  <ul className="mt-6 space-y-3">
                    {planDetails.subscription.features.map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-700 font-medium leading-tight">
                        <CheckCircle2 size={16} className="text-emerald-500 flex-shrink-0 mt-0.5" /> {f}
                      </li>
                    ))}
                  </ul>
                </div>
              </GlassCard>

              {/* Credits Top-up Option */}
              <GlassCard 
                className={`p-6 md:p-8 flex flex-col justify-between h-[380px] transition-all cursor-pointer relative overflow-hidden ${
                  currentTier === "premium" 
                    ? "opacity-60 grayscale cursor-not-allowed border-slate-200" 
                    : selectedPlan === "topup"
                      ? "ring-2 ring-primary border-primary shadow-xl scale-[1.02] bg-blue-50/20"
                      : "hover:border-primary/50 hover:shadow-lg hover:-translate-y-1"
                }`}
                hoverEffect={false}
                onClick={() => currentTier !== "premium" && setSelectedPlan("topup")}
              >
                {selectedPlan === "topup" && currentTier !== "premium" && (
                  <div className="absolute top-0 right-0 bg-primary text-white text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded-bl-xl">Selected</div>
                )}
                
                <div>
                  <div className="w-10 h-10 rounded-xl bg-slate-100 flex items-center justify-center text-slate-600 mb-4">
                    <Zap size={20} />
                  </div>
                  <h4 className="text-xl font-black text-slate-900">{planDetails.topup.title}</h4>
                  <div className="mt-3 flex items-baseline gap-1">
                    <span className="text-4xl font-black text-slate-900 tracking-tighter">{planDetails.topup.price}</span>
                    <span className="text-sm font-bold text-slate-400">/ {planDetails.topup.period}</span>
                  </div>
                  <ul className="mt-6 space-y-3">
                    {planDetails.topup.features.map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-700 font-medium leading-tight">
                        <CheckCircle2 size={16} className="text-emerald-500 flex-shrink-0 mt-0.5" /> {f}
                      </li>
                    ))}
                  </ul>
                </div>
              </GlassCard>
            </div>
          </div>

          <div className="space-y-6">
            <div>
              <h3 className="text-xl font-black text-slate-900 tracking-tighter">2. Secure Checkout</h3>
              <p className="text-xs font-bold text-slate-500 uppercase tracking-wider mt-1">100% Encrypted transactions</p>
            </div>

            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              {currentTier === "premium" ? (
                <div className="text-center py-8">
                  <div className="w-16 h-16 rounded-full bg-slate-100 text-slate-400 mx-auto flex items-center justify-center mb-6">
                    <Lock size={24} />
                  </div>
                  <h4 className="text-xl font-black text-slate-900 mb-2">Purchases Locked</h4>
                  <p className="text-sm text-slate-500 font-medium max-w-[250px] mx-auto mb-8">
                    You already have unlimited access. Additional purchases are disabled.
                  </p>
                </div>
              ) : (
                <>
                  <div className="bg-slate-50 rounded-2xl p-5 border border-slate-200 flex justify-between items-center mb-8 shadow-inner">
                    <div>
                      <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest mb-1">Total Due Today</div>
                      <div className="text-sm font-bold text-slate-900">
                        {selectedPlan === "subscription" ? planDetails.subscription.title : planDetails.topup.title}
                      </div>
                    </div>
                    <div className="text-3xl font-black text-primary tracking-tighter">
                      {selectedPlan === "subscription" ? planDetails.subscription.price : planDetails.topup.price}
                    </div>
                  </div>

                  {loading && (
                    <div className="flex flex-col items-center justify-center py-8 space-y-4">
                      <div className="w-8 h-8 rounded-full border-2 border-slate-200 border-t-primary animate-spin"></div>
                      <div className="text-xs font-bold text-slate-500 uppercase tracking-wider">Processing...</div>
                    </div>
                  )}

                  {!loading && region === "global" && (
                    <div className="relative z-10 min-h-[150px]">
                      <PayPalScriptProvider
                        options={{
                          clientId: PAYPAL_CLIENT_ID,
                          currency: "USD",
                          intent: "capture",
                          components: "buttons",
                        }}
                      >
                        <PayPalButtons
                          style={{ layout: "vertical", shape: "rect", color: "blue" }}
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

                  {!loading && region === "india" && (
                    <div className="relative z-10">
                      <button
                        onClick={handleRazorpayCheckout}
                        className="w-full bg-[#02042b] hover:bg-slate-800 text-white font-bold py-4 px-6 rounded-xl shadow-lg transition-all active:scale-[0.98] flex items-center justify-center gap-2"
                      >
                        <CreditCard size={18} /> Pay with Razorpay
                      </button>
                      <div className="flex items-center justify-center gap-4 mt-6 grayscale opacity-60">
                        <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Supports UPI, Cards, NetBanking</span>
                      </div>
                    </div>
                  )}

                  <div className="flex items-center justify-center gap-2 mt-8 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                    <Shield size={14} /> 256-bit Secure Encryption
                  </div>
                </>
              )}
            </GlassCard>
          </div>
        </div>
      )}

      {/* Credit System Explainer */}
      <StaggerContainer className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-12">
        <StaggerItem>
          <GlassCard className="p-6 md:p-8 h-full bg-blue-50/30 border-blue-100" hoverEffect={false}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center text-blue-600"><Zap size={16} /></div>
              <h4 className="text-sm font-black text-slate-900 tracking-tight uppercase tracking-wider">Uses 1 Credit</h4>
            </div>
            <ul className="space-y-3">
              {[
                "Job matching & skill gap analysis",
                "Generating personalized learning paths",
                "Real-time market trends & salary insights",
                "Asking AI (RAG Chat) questions"
              ].map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-700 font-medium">
                  <span className="text-blue-500 font-black mt-0.5">•</span> {item}
                </li>
              ))}
            </ul>
          </GlassCard>
        </StaggerItem>

        <StaggerItem>
          <GlassCard className="p-6 md:p-8 h-full bg-emerald-50/30 border-emerald-100" hoverEffect={false}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-8 h-8 rounded-lg bg-emerald-100 flex items-center justify-center text-emerald-600"><CheckCircle2 size={16} /></div>
              <h4 className="text-sm font-black text-slate-900 tracking-tight uppercase tracking-wider">Always Free</h4>
            </div>
            <ul className="space-y-3">
              {[
                "Uploading & parsing resumes",
                "Editing your career profile details",
                "Viewing dashboard analytics & history",
                "Browsing course & recommendation library"
              ].map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-700 font-medium">
                  <span className="text-emerald-500 font-black mt-0.5">✓</span> {item}
                </li>
              ))}
            </ul>
          </GlassCard>
        </StaggerItem>
      </StaggerContainer>
    </main>
  );
}
