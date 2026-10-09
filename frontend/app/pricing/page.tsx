import type { Metadata } from "next";
import Link from "next/link";
import { AlertCircle, ArrowLeft, CheckCircle2, CreditCard, Shield } from "lucide-react";

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

export default async function PricingPage() {
  const catalog = await getPublicBillingCatalog();
  const bundles = catalog?.products.filter((product) => product.catalog_visible) ?? [];

  return (
    <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView eventName="pricing_viewed" />
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-muted-foreground hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Public Pricing"
        title="Clear pricing. No automatic renewal."
        subtitle="Finite credit bundles for AI analysis and employer job services. One-time purchases for customers in India, charged in Indian Rupees."
      />

      <FadeIn delay={0.1}>
        {bundles.length ? (
          <div className="grid gap-5 md:grid-cols-3">
            {bundles.map((product) => (
              <GlassCard key={product.sku} className="p-6 flex flex-col" hoverEffect={false}>
                <CreditCard size={22} className="mb-5 text-primary" aria-hidden="true" />
                <h2 className="font-display text-2xl font-normal text-foreground">{product.name}</h2>
                <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{product.description}</p>
                <p className="mt-5 text-4xl font-semibold text-foreground">{new Intl.NumberFormat("en-IN", { style: "currency", currency: product.currency }).format(product.amount_minor / 100)}</p>
                <ul className="mt-6 space-y-3 text-sm">
                  {[`${product.analysis_units} AI analysis units`, `${product.job_service_credits} job service credits`, "One-time payment with no automatic renewal"].map((fact) => (
                    <li key={fact} className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />{fact}</li>
                  ))}
                </ul>
                {catalog?.checkout_enabled === true && catalog.provider === "razorpay" && product.enabled_for_purchase ? (
                  <Link href={`/billing?sku=${encodeURIComponent(product.sku)}`} className="mt-8 inline-flex min-h-11 items-center justify-center rounded-full bg-primary px-4 py-3 text-sm font-bold text-primary-foreground hover:bg-primary/90">Review this bundle</Link>
                ) : <p className="mt-8 text-sm text-muted-foreground">Purchasing is temporarily unavailable.</p>}
              </GlassCard>
            ))}
          </div>
        ) : (
          <GlassCard className="max-w-2xl mx-auto p-8 md:p-10" hoverEffect={false}>
            <div className="flex items-start gap-3" role="status">
              <AlertCircle size={22} className="text-amber-800 flex-shrink-0 mt-0.5" aria-hidden="true" />
              <div>
                <h2 className="font-display text-xl font-normal text-foreground">Purchasing is temporarily unavailable</h2>
                <p className="mt-2 text-sm text-muted-foreground leading-relaxed">New credit bundles are not available yet. Your existing access, balances and accepted purchases remain available. Please check again later or contact support.</p>
                <Link href="/contact" className="mt-3 inline-flex min-h-11 items-center font-semibold text-primary underline">Contact billing support</Link>
              </div>
            </div>
          </GlassCard>
        )}
      </FadeIn>

      <FadeIn delay={0.15}>
        <GlassCard className="p-8 md:p-10 text-foreground leading-relaxed space-y-5" hoverEffect={false}>
          <h2 className="font-display text-xl font-normal text-foreground tracking-tight">How credits work</h2>
          <p>AI analysis units and job service credits are separate, finite software usage allowances. Your account shows each balance, and the confirmation step shows the cost before an operation starts. They are not money or stored value and cannot be withdrawn, transferred or resold.</p>
          <p>Job services use the displayed search or application price. Automatic applications are charged only after a confirmed complete submission. Employer coverage and application routes depend on verified sources and available integrations; opening a company portal is not a completed application.</p>
          <p className="text-sm text-muted-foreground">Existing access and accepted purchases keep their original terms. Contact billing support if a verified payment or completed operation does not appear correctly in your account.</p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.2}>
        <GlassCard className="p-8 md:p-10 text-foreground leading-relaxed space-y-4" hoverEffect={false}>
          <h2 className="font-display text-xl font-normal text-foreground tracking-tight">Payment, tax, and delivery details</h2>
          <ul className="space-y-3 text-sm">
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Seller:</strong> HireWiz, operated by {SITE.operatorName}, trading as HireWiz.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Final total:</strong> The server catalog and order review show the INR amount due before you authorize payment.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>No automatic renewal:</strong> Credit bundles are one-time purchases. We do not automatically charge you again.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Hosted checkout:</strong> Payment credentials are entered with the payment processor used for checkout. HireWiz does not collect or store raw card details, CVV, UPI PIN, or bank-login credentials.</span></li>
            <li className="flex items-start gap-2"><Shield size={16} className="text-primary mt-0.5 flex-shrink-0" /> <span><strong>Digital delivery:</strong> Purchased credits are added to the purchasing account after verified payment. Existing access purchases retain their original delivery terms. See the <Link href="/digital-delivery" className="text-primary underline underline-offset-2 hover:text-foreground transition-colors">Digital Service Delivery &amp; Shipping Policy</Link>.</span></li>
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
