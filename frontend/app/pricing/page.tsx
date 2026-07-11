import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { Crown, Zap, CheckCircle2, Shield, ArrowLeft } from "lucide-react";

export const metadata = {
  title: "Pricing — HireWiz",
  description:
    "HireWiz pricing in Indian Rupees: a monthly Premium plan and a one-time analysis-unit pack. Clear renewal, delivery, and refund terms.",
};

const PREMIUM_FEATURES = [
  "Unlimited job-match reports",
  "Unlimited Ask AI assistant queries",
  "Full market skill-gap analyses",
  "Custom learning paths & project ideas",
  "No analysis-unit consumption",
];

const PACK_FEATURES = [
  "25 analysis units, credited instantly",
  "Units never expire",
  "Use on matches, market analysis, learning & Ask AI",
  "Good for occasional job hunts",
];

export default function PricingPage() {
  return (
    <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Pricing"
        title="Simple, transparent pricing."
        subtitle="Prices are shown in Indian Rupees (₹) for customers in India. Choose a monthly plan or a one-time pack of analysis units — you review every AI suggestion before you use it."
      />

      <FadeIn delay={0.1}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Premium subscription */}
          <GlassCard className="p-8 flex flex-col" hoverEffect={false}>
            <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center text-blue-600 mb-4">
              <Crown size={20} />
            </div>
            <h3 className="text-xl font-black text-white">Premium</h3>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="text-4xl font-black text-white tracking-tighter">₹999</span>
              <span className="text-sm font-bold text-slate-400">/ 30 days</span>
            </div>
            <p className="text-xs font-semibold text-slate-400 mt-2">
              One payment unlocks Premium for 30 days. No stored card and no automatic charge — renew anytime by paying again.
            </p>
            <ul className="mt-6 space-y-3 flex-grow">
              {PREMIUM_FEATURES.map((f, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-200 font-medium leading-tight">
                  <CheckCircle2 size={16} className="text-emerald-500 flex-shrink-0 mt-0.5" /> {f}
                </li>
              ))}
            </ul>
            <Link
              href="/billing"
              className="mt-8 w-full inline-flex justify-center items-center px-4 py-3 rounded-xl bg-primary text-white text-sm font-bold hover:bg-primary/90 transition-colors"
            >
              Sign in to subscribe
            </Link>
          </GlassCard>

          {/* One-time pack */}
          <GlassCard className="p-8 flex flex-col" hoverEffect={false}>
            <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center text-slate-300 mb-4">
              <Zap size={20} />
            </div>
            <h3 className="text-xl font-black text-white">Analysis Units Pack</h3>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="text-4xl font-black text-white tracking-tighter">₹99</span>
              <span className="text-sm font-bold text-slate-400">/ one-time</span>
            </div>
            <p className="text-xs font-semibold text-slate-400 mt-2">
              One-time purchase. No subscription, no auto-renewal.
            </p>
            <ul className="mt-6 space-y-3 flex-grow">
              {PACK_FEATURES.map((f, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-200 font-medium leading-tight">
                  <CheckCircle2 size={16} className="text-emerald-500 flex-shrink-0 mt-0.5" /> {f}
                </li>
              ))}
            </ul>
            <Link
              href="/billing"
              className="mt-8 w-full inline-flex justify-center items-center px-4 py-3 rounded-xl bg-slate-800 border border-slate-600 text-white text-sm font-bold hover:bg-slate-700 transition-colors"
            >
              Sign in to buy units
            </Link>
          </GlassCard>
        </div>
      </FadeIn>

      {/* What is an analysis unit */}
      <FadeIn delay={0.15}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-6" hoverEffect={false}>
          <h2 className="text-xl font-black text-white tracking-tight">What is an analysis unit?</h2>
          <p>
            An <strong>analysis unit</strong> is consumed each time you run an AI-assisted operation. It is a usage
            allowance inside HireWiz only — it is <strong>not money, not a wallet, and not stored value</strong>. Units
            are non-transferable, cannot be withdrawn or resold, and have no cash value. New accounts start with a small
            number of free units so you can try the product.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div>
              <h3 className="text-sm font-black text-white uppercase tracking-wider mb-3">Uses units</h3>
              <ul className="space-y-2 text-sm">
                <li className="flex items-start gap-2"><span className="text-blue-500 font-black mt-0.5">•</span> Job-match report &amp; skill-gap analysis</li>
                <li className="flex items-start gap-2"><span className="text-blue-500 font-black mt-0.5">•</span> Market skill-demand analysis</li>
                <li className="flex items-start gap-2"><span className="text-blue-500 font-black mt-0.5">•</span> Learning-path generation</li>
                <li className="flex items-start gap-2"><span className="text-blue-500 font-black mt-0.5">•</span> Ask AI (chat) and bullet optimization</li>
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-black text-white uppercase tracking-wider mb-3">Always free</h3>
              <ul className="space-y-2 text-sm">
                <li className="flex items-start gap-2"><span className="text-emerald-500 font-black mt-0.5">✓</span> Uploading &amp; parsing resumes</li>
                <li className="flex items-start gap-2"><span className="text-emerald-500 font-black mt-0.5">✓</span> Editing your career profile</li>
                <li className="flex items-start gap-2"><span className="text-emerald-500 font-black mt-0.5">✓</span> Viewing dashboard &amp; history</li>
                <li className="flex items-start gap-2"><span className="text-emerald-500 font-black mt-0.5">✓</span> Browsing the course library</li>
              </ul>
            </div>
          </div>
          <p className="text-sm text-slate-400">
            Most operations use a single unit; heavier operations use more — for example, a market skill analysis uses
            5 units and generating a full tailored resume uses 10 units. Premium members have unlimited operations and
            do not consume units.
          </p>
        </GlassCard>
      </FadeIn>

      {/* Billing details */}
      <FadeIn delay={0.2}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <h2 className="text-xl font-black text-white tracking-tight">Billing details</h2>
          <ul className="space-y-3 text-sm">
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Currency &amp; tax:</strong> Prices are in Indian Rupees (₹). HireWiz is operated by an individual and is <strong>not currently GST-registered</strong>, so no GST is added.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Renewal:</strong> Premium is a one-time ₹999 payment that unlocks access for 30 days. There is no stored mandate and no automatic charge — it simply expires unless you choose to pay again. The units pack is also one-time.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Delivery:</strong> Access is digital and is activated in your HireWiz account after your payment is confirmed. See our <Link href="/digital-delivery" className="text-primary hover:underline">Digital Service Delivery Policy</Link>.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Cancellation &amp; refunds:</strong> Cancel anytime by emailing <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>. See our <Link href="/refund" className="text-primary hover:underline">Refund &amp; Cancellation Policy</Link>.</span></li>
          </ul>
          <div className="pt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs font-semibold text-slate-400 border-t border-slate-700/60">
            <Link href="/terms" className="hover:text-primary">Terms of Service</Link>
            <Link href="/privacy" className="hover:text-primary">Privacy Policy</Link>
            <Link href="/refund" className="hover:text-primary">Refund &amp; Cancellation</Link>
            <Link href="/digital-delivery" className="hover:text-primary">Digital Delivery</Link>
            <Link href="/contact" className="hover:text-primary">Contact</Link>
          </div>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
