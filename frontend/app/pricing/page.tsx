import type { Metadata } from "next";
import Link from "next/link";
import { AlertCircle, ArrowLeft, CheckCircle2, Crown, Shield } from "lucide-react";

import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import TrackEventOnView from "../../components/TrackEventOnView";
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
  "Role-specific interview preparation and evidence-backed resume tailoring",
  "Market skill-demand analyses based on the available job-posting sample",
  "Skill priorities and career workspace insights",
  "No analysis-unit deductions while Premium is active",
];

export default async function PricingPage() {
  const catalog = await getPublicBillingCatalog();
  const premium = catalog?.products.find(
    (product) => product.sku === "premium_30d" && product.catalog_visible,
  );
  const servicePack = catalog?.products.find((product) => product.sku === "job_service_500" && product.catalog_visible);
  const canPurchase = Boolean(catalog?.checkout_enabled && premium?.enabled_for_purchase);

  return (
    <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView eventName="pricing_viewed" />
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-muted-foreground hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Public Pricing"
        title="Clear pricing. No automatic renewal."
        subtitle="Choose a 30-day Premium pass or prepaid job service credits. One-time purchases for customers in India, charged in Indian Rupees."
      />

      <FadeIn delay={0.1}>
        {premium ? (
          <GlassCard className="max-w-2xl mx-auto p-8 md:p-10 flex flex-col" hoverEffect={false}>
            <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center text-primary mb-5">
              <Crown size={22} />
            </div>
            <h2 className="font-display text-2xl font-normal text-foreground">{premium.name}</h2>
            <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{premium.description}</p>
            <div className="mt-5 flex items-baseline gap-2">
              <span className="text-5xl font-semibold text-foreground tracking-tighter">{premium.amount_display}</span>
              <span className="text-sm font-bold text-muted-foreground">one-time</span>
            </div>
            <p className="text-sm font-semibold text-foreground mt-3">
              {premium.duration_days} days of access · {premium.currency} · no trial · no subscription · no auto-renewal
            </p>

            <ul className="mt-7 space-y-3">
              {PREMIUM_FEATURES.map((feature) => (
                <li key={feature} className="flex items-start gap-2 text-sm text-foreground font-medium leading-relaxed">
                  <CheckCircle2 size={16} className="text-primary flex-shrink-0 mt-0.5" /> {feature}
                </li>
              ))}
            </ul>

            {canPurchase ? (
              <Link
                href="/billing"
                className="mt-8 w-full inline-flex justify-center items-center px-4 py-3 rounded-full bg-primary text-primary-foreground text-sm font-bold hover:bg-primary/90 transition-colors"
              >
                Sign in to purchase
              </Link>
            ) : (
              <div className="mt-8 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                Paid checkout is not currently enabled. You can still create a free account and review the product before purchasing becomes available.
                <Link href="/register" className="mt-3 inline-flex font-bold text-amber-800 hover:underline">Create a free account</Link>
              </div>
            )}
          </GlassCard>
        ) : (
          <GlassCard className="max-w-2xl mx-auto p-8 md:p-10" hoverEffect={false}>
            <div className="flex items-start gap-3">
              <AlertCircle size={22} className="text-amber-800 flex-shrink-0 mt-0.5" />
              <div>
                <h2 className="font-display text-xl font-normal text-foreground">Pricing is temporarily unavailable</h2>
                <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
                  We could not load the server-owned product catalog. No checkout can be started from this page. Please try again later or contact support.
                </p>
              </div>
            </div>
          </GlassCard>
        )}
      </FadeIn>

      {servicePack ? <GlassCard className="max-w-2xl mx-auto p-8 md:p-10" hoverEffect={false}><p className="data-label">Employer search and apply</p><h2 className="font-display mt-2 text-2xl">{servicePack.name}</h2><p className="mt-3 text-sm leading-6 text-muted-foreground">{servicePack.description}</p><p className="mt-5 text-4xl font-semibold">{servicePack.amount_display}</p><p className="mt-3 text-sm leading-6">{servicePack.entitlement_quantity} service credits. Separate from complimentary analysis units and Premium access. Search prices and automatic-apply prices are shown before use; automatic apply is charged only on a confirmed complete submission.</p><p className="mt-3 text-xs leading-5 text-muted-foreground">Employer coverage and automatic submission depend on currently verified sources and permissioned integrations. A manual portal handoff is not a completed application.</p><Link href="/billing" className="mt-5 inline-flex font-semibold text-primary underline underline-offset-4">{catalog?.checkout_enabled && servicePack.enabled_for_purchase ? "Review credit pack" : "Review availability"}</Link></GlassCard> : null}

      <FadeIn delay={0.15}>
        <GlassCard className="p-8 md:p-10 text-foreground leading-relaxed space-y-5" hoverEffect={false}>
          <h2 className="font-display text-xl font-normal text-foreground tracking-tight">Included analysis units</h2>
          <p>
            New free accounts receive <strong>50 complimentary analysis units</strong> for metered AI-assisted
            operations. They do not refresh on a schedule or expire while the account remains open; they are used
            until the balance reaches zero. An analysis unit is a feature-use allowance inside HireWiz. It is not
            money, a wallet, virtual currency, or stored value. Units are non-transferable, cannot be withdrawn or
            resold, and have no cash value.
          </p>
          <p>
            Most metered operations use one unit. A market skill-demand analysis currently uses five units and a
            full tailored-resume draft uses ten units. The confirmation button shows the unit cost before an
            operation starts. Standalone analysis units are not sold. Paid job service credit packs are a separate allowance for employer search and confirmed automatic applications.
          </p>
          <p className="text-sm text-muted-foreground">
            If a technical failure consumes units without delivering a result, contact support with the time and
            operation details so the transaction can be reviewed; restoration is not automatic. Premium access
            removes unit deductions while the 30-day pass remains active. Deleting the account removes unused units.
          </p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.2}>
        <GlassCard className="p-8 md:p-10 text-foreground leading-relaxed space-y-4" hoverEffect={false}>
          <h2 className="font-display text-xl font-normal text-foreground tracking-tight">Payment, tax, and delivery details</h2>
          <ul className="space-y-3 text-sm">
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Seller:</strong> HireWiz, operated by {SITE.operatorName}, trading as HireWiz.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Final total:</strong> ₹999 INR is the full amount due for this pass. HireWiz does not add a separate fee or tax at checkout.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>No automatic renewal:</strong> The pass expires after 30 days. We do not store a mandate or automatically charge you again.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Hosted checkout:</strong> Payment credentials are entered with the payment processor used for checkout. HireWiz does not collect or store raw card details, CVV, UPI PIN, or bank-login credentials.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Digital delivery:</strong> Premium access is normally added to the purchasing account after confirmed payment. See the <Link href="/digital-delivery" className="text-primary underline underline-offset-2 hover:text-foreground transition-colors">Digital Service Delivery &amp; Shipping Policy</Link>.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Refunds:</strong> Eligibility and the request process are set out in the <Link href="/refund" className="text-primary underline underline-offset-2 hover:text-foreground transition-colors">Refund &amp; Cancellation Policy</Link>.</span></li>
          </ul>
          <div className="pt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs font-semibold text-muted-foreground border-t border-border">
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
