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
import { CreditCard, Zap, CheckCircle2, Shield, Globe, MapPin, AlertCircle, Crown, Lock, Map, AlertTriangle } from "lucide-react";
import { COUNTRIES } from "../../lib/countries";

const PAYPAL_CLIENT_ID = process.env.NEXT_PUBLIC_PAYPAL_CLIENT_ID || "test";
const CASHFREE_MODE = process.env.NEXT_PUBLIC_CASHFREE_MODE || "sandbox";
// The global (USD/PayPal) lane stays hidden until an international provider is
// approved and activated for production.
const ENABLE_GLOBAL_PAYPAL = false;

export default function BillingPage() {
  const router = useRouter();
  const [region, setRegion] = useState<"global" | "india">("india");
  const [selectedCountry, setSelectedCountry] = useState<string>("");
  const [gpsCountry, setGpsCountry] = useState<string | null>(null);
  const [showWarning, setShowWarning] = useState(false);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState<"subscription" | "topup">("subscription");
  const [paymentSuccess, setPaymentSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentTier, setCurrentTier] = useState<string>("free");
  const [creditBalance, setCreditBalance] = useState<number>(0);
  const [phone, setPhone] = useState<string>("");

  useEffect(() => {
    // Get GPS location
    if ("geolocation" in navigator) {
      navigator.geolocation.getCurrentPosition(
        async (position) => {
          try {
            const { latitude, longitude } = position.coords;
            const res = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${latitude}&longitude=${longitude}&localityLanguage=en`);
            const data = await res.json();
            if (data.countryCode) {
              setGpsCountry(data.countryCode);
              // Auto select if first time
              setSelectedCountry(data.countryCode);
              setRegion(data.countryCode === "IN" ? "india" : "global");
            }
          } catch (err) {
            console.error("Geocoding failed", err);
          }
        },
        (error) => {
          console.error("GPS access denied or failed", error);
        }
      );
    }
  }, []);

  const handleCountryChange = (code: string) => {
    setSelectedCountry(code);
    if (gpsCountry && code !== gpsCountry) {
      setShowWarning(true);
    } else {
      setRegion(code === "IN" ? "india" : "global");
    }
  };

  const confirmCountryMismatch = () => {
    setShowWarning(false);
    setRegion(selectedCountry === "IN" ? "india" : "global");
  };

  const cancelCountryMismatch = () => {
    setShowWarning(false);
    if (gpsCountry) {
      setSelectedCountry(gpsCountry);
      setRegion(gpsCountry === "IN" ? "india" : "global");
    }
  };

  useEffect(() => {
    let mounted = true;
    apiGet<any>("/auth/profile")
      .then((data) => {
        if (mounted) {
          setCurrentTier(data.tier || "free");
          setCreditBalance(data.ai_credits ?? 0);
          setPhone(data.phone || "");
        }
      })
      .catch((err) => {
        console.error("Failed to load profile for billing page:", err);
      });
    return () => {
      mounted = false;
    };
  }, []);

  // When the customer returns from Cashfree checkout, confirm the payment.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const returnedOrderId = params.get("order_id");
    if (!returnedOrderId) return;

    setLoading(true);
    apiPostJson<{ status: string; type?: string }>("/billing/cashfree/verify-payment", { order_id: returnedOrderId })
      .then((r) => {
        if (r.status === "success") {
          setPaymentSuccess(true);
          window.dispatchEvent(new Event("refresh_credits"));
          setTimeout(() => router.push("/dashboard"), 3000);
        } else {
          setError("Your payment is still processing. If you were charged, your access will activate shortly.");
        }
      })
      .catch((err: any) => setError(err.message || "Could not verify your payment. Please contact support with your order ID."))
      .finally(() => {
        setLoading(false);
        window.history.replaceState({}, "", "/billing");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      price: region === "india" ? "₹99" : "$3.99",
      period: "one-time",
      features: [
        "25 AI Operation credits instantly",
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
        credits: selectedPlan === "topup" ? 25 : 0,
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
        credits: selectedPlan === "topup" ? 25 : 0,
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

  async function handleCashfreeCheckout() {
    setLoading(true);
    setError(null);
    try {
      const res = await apiPostJson<{ payment_session_id: string; order_id: string; mode: string }>(
        "/billing/cashfree/create-order",
        { type: selectedPlan, customer_phone: phone || undefined }
      );

      // Mock mode (no live Cashfree keys yet): confirm directly so the flow is testable.
      if (res.mode === "mock" || res.payment_session_id.startsWith("mock_")) {
        await apiPostJson("/billing/cashfree/verify-payment", { order_id: res.order_id });
        setPaymentSuccess(true);
        window.dispatchEvent(new Event("refresh_credits"));
        setTimeout(() => router.push("/dashboard"), 3000);
        return;
      }

      const cashfreeFactory = (window as any).Cashfree;
      if (!cashfreeFactory) {
        setError("Payment library is still loading. Please wait a moment and try again.");
        return;
      }
      const cashfree = cashfreeFactory({ mode: CASHFREE_MODE });
      // Redirects to Cashfree, then back to /billing?order_id=... where we verify.
      await cashfree.checkout({ paymentSessionId: res.payment_session_id, redirectTarget: "_self" });
    } catch (err: any) {
      setError(err.message || "Failed to start checkout. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <Script src="https://sdk.cashfree.com/js/v3/cashfree.js" strategy="afterInteractive" />
      
      <PageHeader 
        badge="Commercial Account"
        title="Upgrade your Career Command Center."
        subtitle="Select a commercial tier to unlock advanced AI parsing, job matches, and market trend tracking."
      />

      {/* Current Status Bar */}
      <GlassCard className="p-6 md:p-8 bg-gradient-to-r from-slate-900 to-blue-950 border-slate-800 text-white flex flex-col md:flex-row items-start md:items-center justify-between gap-6 overflow-hidden relative" hoverEffect={false}>
        <div className="absolute top-0 right-0 w-64 h-64 bg-primary/20 blur-3xl rounded-full -mr-20 -mt-20 pointer-events-none"></div>
        
        <div className="relative z-10 flex items-center gap-5">
          <div className="w-14 h-14 rounded-2xl bg-slate-950/10 border border-white/20 flex items-center justify-center backdrop-blur-sm">
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
          <div className="w-px h-10 bg-slate-950/10"></div>
          <div className="text-xs text-slate-400 font-bold max-w-[120px] leading-snug">
            {currentTier === "premium" ? "Unlimited Operations" : "Pay-As-You-Go Operations"}
          </div>
        </div>
      </GlassCard>

      {paymentSuccess && (
        <FadeIn>
          <GlassCard className="p-10 text-center bg-emerald-900/30/50 border-emerald-800" hoverEffect={false}>
            <div className="w-20 h-20 mx-auto rounded-full bg-emerald-100 flex items-center justify-center text-emerald-400 mb-6">
              <CheckCircle2 size={40} />
            </div>
            <h2 className="text-3xl font-black text-white tracking-tighter mb-2">Payment Completed!</h2>
            <p className="text-slate-300 font-medium max-w-md mx-auto">Your tier and credit privileges have been successfully activated. We are redirecting you to your dashboard...</p>
          </GlassCard>
        </FadeIn>
      )}

      {error && (
        <FadeIn>
          <div className="flex items-center gap-2 text-sm text-rose-400 bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3 shadow-sm max-w-4xl mx-auto">
            <AlertCircle size={16} /> {error}
          </div>
        </FadeIn>
      )}

      {!paymentSuccess && (
        <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-8">
          <div className="space-y-8">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div>
                <h3 className="text-xl font-black text-white tracking-tighter">1. Select Package</h3>
                <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mt-1">Pricing adjusts automatically to your location</p>
              </div>
              
              <div className="flex flex-col gap-2 relative z-40">
                <div 
                  className="relative"
                  tabIndex={0}
                  onBlur={(e) => {
                    if (!e.currentTarget.contains(e.relatedTarget)) {
                      setIsDropdownOpen(false);
                    }
                  }}
                >
                  <button 
                    onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                    className="flex items-center justify-between w-full min-w-[240px] gap-3 bg-slate-900 border border-slate-700 hover:border-slate-600 px-4 py-3 rounded-xl transition-all shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
                  >
                    <div className="flex items-center gap-3">
                      <Map size={16} className={selectedCountry ? "text-primary" : "text-slate-400"} />
                      <span className={`text-sm font-bold ${selectedCountry ? "text-white" : "text-slate-400"}`}>
                        {selectedCountry ? COUNTRIES.find(c => c.code === selectedCountry)?.name : "Select your country..."}
                      </span>
                    </div>
                    <svg className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${isDropdownOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7"></path></svg>
                  </button>

                  {isDropdownOpen && (
                    <div className="absolute top-full mt-2 w-full max-h-[280px] overflow-y-auto bg-slate-800 border border-slate-700 rounded-xl shadow-2xl z-50 py-2 scrollbar-thin">
                      {COUNTRIES.map(c => (
                        <button
                          key={c.code}
                          onClick={() => {
                            handleCountryChange(c.code);
                            setIsDropdownOpen(false);
                          }}
                          className={`w-full text-left px-4 py-2.5 text-sm transition-colors flex items-center justify-between
                            ${selectedCountry === c.code ? 'bg-primary/20 text-white font-bold' : 'text-slate-300 hover:bg-slate-700 hover:text-white'}`
                          }
                        >
                          {c.name}
                          {selectedCountry === c.code && <CheckCircle2 size={14} className="text-primary" />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                
                {showWarning && (
                  <div className="absolute top-full mt-2 right-0 w-72 bg-slate-800 border border-amber-500/50 p-4 rounded-xl shadow-2xl z-50">
                    <div className="flex items-start gap-3">
                      <AlertTriangle size={20} className="text-amber-500 shrink-0 mt-0.5" />
                      <div>
                        <h4 className="text-sm font-bold text-white mb-1">Location Mismatch</h4>
                        <p className="text-xs text-slate-300 mb-3 leading-relaxed">
                          Your selected country doesn't match your physical GPS location. Proceeding may cause payment failures.
                        </p>
                        <div className="flex gap-2">
                          <button onClick={confirmCountryMismatch} className="text-xs font-bold bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 px-3 py-1.5 rounded-lg transition-colors">Proceed Anyway</button>
                          <button onClick={cancelCountryMismatch} className="text-xs font-bold bg-slate-700 text-slate-300 hover:bg-slate-600 px-3 py-1.5 rounded-lg transition-colors">Go Back</button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Premium Plan Card */}
              <GlassCard 
                className={`p-6 md:p-8 flex flex-col justify-between h-[380px] transition-all cursor-pointer relative overflow-hidden ${
                  currentTier === "premium" 
                    ? "opacity-60 grayscale cursor-not-allowed border-slate-700" 
                    : selectedPlan === "subscription"
                      ? "ring-2 ring-primary border-primary shadow-xl scale-[1.02] bg-blue-900/30/20"
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
                  <h4 className="text-xl font-black text-white">{planDetails.subscription.title}</h4>
                  <div className="mt-3 flex items-baseline gap-1">
                    <span className="text-4xl font-black text-white tracking-tighter">{planDetails.subscription.price}</span>
                    <span className="text-sm font-bold text-slate-400">/ {planDetails.subscription.period}</span>
                  </div>
                  <ul className="mt-6 space-y-3">
                    {planDetails.subscription.features.map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-200 font-medium leading-tight">
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
                    ? "opacity-60 grayscale cursor-not-allowed border-slate-700" 
                    : selectedPlan === "topup"
                      ? "ring-2 ring-primary border-primary shadow-xl scale-[1.02] bg-blue-900/30/20"
                      : "hover:border-primary/50 hover:shadow-lg hover:-translate-y-1"
                }`}
                hoverEffect={false}
                onClick={() => currentTier !== "premium" && setSelectedPlan("topup")}
              >
                {selectedPlan === "topup" && currentTier !== "premium" && (
                  <div className="absolute top-0 right-0 bg-primary text-white text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded-bl-xl">Selected</div>
                )}
                
                <div>
                  <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center text-slate-300 mb-4">
                    <Zap size={20} />
                  </div>
                  <h4 className="text-xl font-black text-white">{planDetails.topup.title}</h4>
                  <div className="mt-3 flex items-baseline gap-1">
                    <span className="text-4xl font-black text-white tracking-tighter">{planDetails.topup.price}</span>
                    <span className="text-sm font-bold text-slate-400">/ {planDetails.topup.period}</span>
                  </div>
                  <ul className="mt-6 space-y-3">
                    {planDetails.topup.features.map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-200 font-medium leading-tight">
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
              <h3 className="text-xl font-black text-white tracking-tighter">2. Secure Checkout</h3>
              <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mt-1">100% Encrypted transactions</p>
            </div>

            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              {currentTier === "premium" ? (
                <div className="text-center py-8">
                  <div className="w-16 h-16 rounded-full bg-slate-800 text-slate-400 mx-auto flex items-center justify-center mb-6">
                    <Lock size={24} />
                  </div>
                  <h4 className="text-xl font-black text-white mb-2">Purchases Locked</h4>
                  <p className="text-sm text-slate-400 font-medium max-w-[250px] mx-auto mb-8">
                    You already have unlimited access. Additional purchases are disabled.
                  </p>
                </div>
              ) : (
                <>
                  <div className="bg-slate-900/50 rounded-2xl p-5 border border-slate-700 flex justify-between items-center mb-8 shadow-inner">
                    <div>
                      <div className="text-[10px] text-slate-400 font-black uppercase tracking-widest mb-1">Total Due Today</div>
                      <div className="text-sm font-bold text-white">
                        {selectedPlan === "subscription" ? planDetails.subscription.title : planDetails.topup.title}
                      </div>
                    </div>
                    <div className="text-3xl font-black text-primary tracking-tighter">
                      {selectedPlan === "subscription" ? planDetails.subscription.price : planDetails.topup.price}
                    </div>
                  </div>

                  {loading && (
                    <div className="flex flex-col items-center justify-center py-8 space-y-4">
                      <div className="w-8 h-8 rounded-full border-2 border-slate-700 border-t-primary animate-spin"></div>
                      <div className="text-xs font-bold text-slate-400 uppercase tracking-wider">Processing...</div>
                    </div>
                  )}

                  {!loading && region === "india" && (
                    <div className="relative z-10 space-y-4">
                      <div>
                        <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5 px-1">Mobile Number (for payment receipt)</label>
                        <input
                          value={phone}
                          onChange={(e) => setPhone(e.target.value)}
                          inputMode="tel"
                          placeholder="10-digit mobile number"
                          className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10"
                        />
                      </div>
                      <button
                        onClick={handleCashfreeCheckout}
                        className="w-full bg-primary hover:bg-primary/90 text-white font-bold py-4 px-6 rounded-xl shadow-lg transition-all active:scale-[0.98] flex items-center justify-center gap-2"
                      >
                        <CreditCard size={18} /> Pay Securely (Cashfree)
                      </button>
                      <div className="flex items-center justify-center gap-4 grayscale opacity-60">
                        <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">UPI · Cards · NetBanking · Wallets</span>
                      </div>
                    </div>
                  )}

                  {!loading && region === "global" && (
                    ENABLE_GLOBAL_PAYPAL ? (
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
                    ) : (
                      <div className="relative z-10 rounded-xl border border-slate-700 bg-slate-900/50 p-5 text-center">
                        <Globe size={20} className="mx-auto text-slate-400 mb-2" />
                        <p className="text-sm font-semibold text-white mb-1">International checkout coming soon</p>
                        <p className="text-xs text-slate-400">Paid plans are currently available for customers in India. Please check back soon.</p>
                      </div>
                    )
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
          <GlassCard className="p-6 md:p-8 h-full bg-blue-900/30/30 border-blue-800" hoverEffect={false}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center text-blue-600"><Zap size={16} /></div>
              <h4 className="text-sm font-black text-white tracking-tight uppercase tracking-wider">Uses 1 Credit</h4>
            </div>
            <ul className="space-y-3">
              {[
                "Job matching & skill gap analysis",
                "Generating personalized learning paths",
                "Real-time market trends & salary insights",
                "Asking AI (RAG Chat) questions"
              ].map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-200 font-medium">
                  <span className="text-blue-500 font-black mt-0.5">•</span> {item}
                </li>
              ))}
            </ul>
          </GlassCard>
        </StaggerItem>

        <StaggerItem>
          <GlassCard className="p-6 md:p-8 h-full bg-emerald-900/30/30 border-emerald-800" hoverEffect={false}>
            <div className="flex items-center gap-3 mb-4">
              <div className="w-8 h-8 rounded-lg bg-emerald-100 flex items-center justify-center text-emerald-400"><CheckCircle2 size={16} /></div>
              <h4 className="text-sm font-black text-white tracking-tight uppercase tracking-wider">Always Free</h4>
            </div>
            <ul className="space-y-3">
              {[
                "Uploading & parsing resumes",
                "Editing your career profile details",
                "Viewing dashboard analytics & history",
                "Browsing course & recommendation library"
              ].map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-200 font-medium">
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
