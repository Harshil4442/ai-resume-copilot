import type { Metadata } from "next";
import Link from "next/link";
import { AlertCircle, ArrowLeft, CheckCircle2, Crown, Shield } from "lucide-react";

import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import { getPublicBillingCatalog } from "../../lib/billingCatalog";
import { SITE } from "../../lib/site";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Pricing",
  description:
    "Public HireWiz pricing for customers in India, including duration, renewal, delivery, usage, and refund information.",
  alternates: { canonical: "/pricing" },
};

const PREMIUM_FEATURES = [
  "Unlimited job-description comparison reports during the 30-day access period",
  "Unlimited Ask AI queries and resume-bullet suggestions during the access period",
  "Market skill-demand analyses based on the available job-posting sample",
  "Learning-path and project suggestions",
  "No analysis-unit deductions while Premium is active",
];

export default async function PricingPage() {
  const catalog = await getPublicBillingCatalog();
  const premium = catalog?.products.find(
    (product) => product.sku === "premium_30d" && product.catalog_visible,
  );
  const canPurchase = Boolean(catalog?.checkout_enabled && premium?.enabled_for_purchase);

  return (
    <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Public Pricing"
        title="One clear price. No automatic renewal."
        subtitle="HireWiz currently offers one paid product for customers in India: a one-time, 30-day Premium access pass charged in Indian Rupees."
      />

      <FadeIn delay={0.1}>
        {premium ? (
          <GlassCard className="max-w-2xl mx-auto p-8 md:p-10 flex flex-col" hoverEffect={false}>
            <div className="w-11 h-11 rounded-xl bg-blue-500/15 flex items-center justify-center text-blue-400 mb-5">
              <Crown size={22} />
            </div>
            <h2 className="text-2xl font-black text-white">{premium.name}</h2>
            <p className="mt-2 text-sm text-slate-400 leading-relaxed">{premium.description}</p>
            <div className="mt-5 flex items-baseline gap-2">
              <span className="text-5xl font-black text-white tracking-tighter">{premium.amount_display}</span>
              <span className="text-sm font-bold text-slate-400">one-time</span>
            </div>
            <p className="text-sm font-semibold text-slate-300 mt-3">
              {premium.duration_days} days of access · {premium.currency} · no trial · no subscription · no auto-renewal
            </p>

            <ul className="mt-7 space-y-3">
              {PREMIUM_FEATURES.map((feature) => (
                <li key={feature} className="flex items-start gap-2 text-sm text-slate-200 font-medium leading-relaxed">
                  <CheckCircle2 size={16} className="text-emerald-400 flex-shrink-0 mt-0.5" /> {feature}
                </li>
              ))}
            </ul>

            {canPurchase ? (
              <Link
                href="/billing"
                className="mt-8 w-full inline-flex justify-center items-center px-4 py-3 rounded-xl bg-primary text-white text-sm font-bold hover:bg-primary/90 transition-colors"
              >
                Sign in to purchase
              </Link>
            ) : (
              <div className="mt-8 rounded-xl border border-amber-800/70 bg-amber-950/30 p-4 text-sm text-amber-100">
                Paid checkout is not currently enabled. You can still create a free account and review the product before purchasing becomes available.
                <Link href="/register" className="mt-3 inline-flex font-bold text-amber-300 hover:underline">Create a free account</Link>
              </div>
            )}
          </GlassCard>
        ) : (
          <GlassCard className="max-w-2xl mx-auto p-8 md:p-10" hoverEffect={false}>
            <div className="flex items-start gap-3">
              <AlertCircle size={22} className="text-amber-400 flex-shrink-0 mt-0.5" />
              <div>
                <h2 className="text-xl font-black text-white">Pricing is temporarily unavailable</h2>
                <p className="mt-2 text-sm text-slate-400 leading-relaxed">
                  We could not load the server-owned product catalog. No checkout can be started from this page. Please try again later or contact support.
                </p>
              </div>
            </div>
          </GlassCard>
        )}
      </FadeIn>

      <FadeIn delay={0.15}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-5" hoverEffect={false}>
          <h2 className="text-xl font-black text-white tracking-tight">Included analysis units</h2>
          <p>
            New free accounts receive <strong>20 complimentary analysis units</strong> for metered AI-assisted
            operations. They do not refresh on a schedule or expire while the account remains open; they are used
            until the balance reaches zero. An analysis unit is a feature-use allowance inside HireWiz. It is not
            money, a wallet, virtual currency, or stored value. Units are non-transferable, cannot be withdrawn or
            resold, and have no cash value.
          </p>
          <p>
            Most metered operations use one unit. A market skill-demand analysis currently uses five units and a
            full tailored-resume draft uses ten units. The confirmation button shows the unit cost before an
            operation starts. At launch, HireWiz does not sell standalone units or top-up packs.
          </p>
          <p className="text-sm text-slate-400">
            If a technical failure consumes units without delivering a result, contact support with the time and
            operation details so the transaction can be reviewed; restoration is not automatic. Premium access
            removes unit deductions while the 30-day pass remains active. Deleting the account removes unused units.
          </p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.2}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <h2 className="text-xl font-black text-white tracking-tight">Payment, tax, and delivery details</h2>
          <ul className="space-y-3 text-sm">
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Seller:</strong> HireWiz, operated by {SITE.operatorName}, trading as HireWiz.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Final total:</strong> ₹999 INR is the full amount due for this pass. HireWiz does not add a separate fee or tax at checkout.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>No automatic renewal:</strong> The pass expires after 30 days. We do not store a mandate or automatically charge you again.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Hosted checkout:</strong> Payment credentials are entered with the payment processor used for checkout. HireWiz does not collect or store raw card details, CVV, UPI PIN, or bank-login credentials.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Digital delivery:</strong> Premium access is normally added to the purchasing account after confirmed payment. See the <Link href="/digital-delivery" className="text-primary hover:underline">Digital Service Delivery &amp; Shipping Policy</Link>.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Refunds:</strong> Eligibility and the request process are set out in the <Link href="/refund" className="text-primary hover:underline">Refund &amp; Cancellation Policy</Link>.</span></li>
          </ul>
          <div className="pt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs font-semibold text-slate-400 border-t border-slate-700/60">
            <Link href="/terms" className="hover:text-primary">Terms of Service</Link>
            <Link href="/privacy" className="hover:text-primary">Privacy Policy</Link>
            <Link href="/refund" className="hover:text-primary">Refund &amp; Cancellation</Link>
            <Link href="/digital-delivery" className="hover:text-primary">Digital Delivery</Link>
            <Link href="/contact" className="hover:text-primary">Support &amp; Grievance</Link>
          </div>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
