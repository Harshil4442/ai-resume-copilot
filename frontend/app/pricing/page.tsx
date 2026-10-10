import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, Shield } from "lucide-react";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import TrackEventOnView from "../../components/TrackEventOnView";
import { getPublicBillingCatalog } from "../../lib/billingCatalog";
import { SITE } from "../../lib/site";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Pricing", description: "Three finite HireWiz prepaid packs for employer job search, applications and optional resume AI. Clear INR prices with no automatic renewal.", alternates: { canonical: "/pricing" } };

export default async function PricingPage() {
  const catalog = await getPublicBillingCatalog();
  const products = catalog?.products.filter(p => p.catalog_visible && p.entitlement_kind === "credit_bundle") ?? [];
  return <main className="mx-auto w-full max-w-[72rem] space-y-10 px-4 py-12 sm:px-6 md:px-8">
    <TrackEventOnView eventName="pricing_viewed" />
    <Link href="/" className="inline-flex items-center text-sm font-semibold text-muted-foreground hover:text-primary"><ArrowLeft size={16} className="mr-2" /> Back to Home</Link>
    <PageHeader badge="Public pricing" title="A clear plan for your next move." subtitle="Three finite prepaid packs. Separate job-service and AI balances. One-time INR payments for customers in India, with no automatic renewal." />
    <FadeIn><div className="grid gap-5 md:grid-cols-3">{products.length ? products.map(p => <GlassCard key={p.sku} className="flex flex-col p-6 sm:p-8" hoverEffect={false}>
      <h2 className="font-display text-2xl">{p.name}</h2><p className="mt-4 text-4xl font-semibold">{p.amount_display}</p><p className="mt-2 text-xs text-muted-foreground">INR total · one-time purchase</p>
      <ul className="my-6 space-y-3 text-sm">{[`${p.entitlement_quantity} job service credits`, `${p.analysis_units} AI analysis units`, "Full tailored-resume draft: 2 analysis units", "Review every AI quote before generation", "No subscription or automatic renewal"].map(f => <li key={f} className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 shrink-0 text-primary" />{f}</li>)}</ul>
      <Link href={`/billing?sku=${p.sku}`} className="mt-auto inline-flex min-h-11 items-center justify-center rounded-full bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground">{catalog?.checkout_enabled && p.enabled_for_purchase ? "Review pack" : "Review availability"}</Link>
    </GlassCard>) : <GlassCard className="p-6 md:col-span-3" hoverEffect={false}><h2 className="font-display text-xl">Pricing is temporarily unavailable</h2><p className="mt-2 text-sm text-muted-foreground">Please try again later or contact support. No checkout can start without the server-owned catalog.</p></GlassCard>}</div></FadeIn>
    {catalog?.availability_message ? <p role="status" className="rounded-xl border border-border p-4 text-sm text-muted-foreground">{catalog.availability_message}</p> : null}
    <GlassCard className="space-y-4 p-6 sm:p-8" hoverEffect={false}><h2 className="font-display text-2xl">Use each balance with confidence</h2>
      <p className="text-sm leading-7">Job service credits pay for newly delivered verified employer jobs and confirmed automatic applications. {catalog?.service_prices ? `Search uses ${catalog.service_prices.search_credits_per_job} credit per new job; confirmed automatic apply uses ${catalog.service_prices.apply_credits_per_job} credits per job.` : "Current search and application prices are shown before use."} Unused search reservations are returned. A manual employer-portal handoff has no automatic-application fee. Unknown submission outcomes stay reserved until reconciled.</p>
      <p className="text-sm leading-7">Optional AI has a separate analysis-unit quote: comparison and interview preparation use 1 unit, market analysis 5, and a full tailored-resume draft 2. Skill ROI uses no analysis units. New free accounts start with 50 finite complimentary units. Free and existing legacy-access AI work depend on a limited funded pool; service availability can pause when funding is exhausted.</p>
      <p className="text-sm leading-7">Balances do not refresh automatically. They are non-transferable software allowances with no cash value. Accepted older orders retain their purchased terms. Employer coverage and automatic submission depend on verified sources and permitted integrations; manual handoff is not a completed application.</p>
    </GlassCard>
    <GlassCard className="space-y-4 p-6 sm:p-8" hoverEffect={false}><h2 className="font-display text-2xl">Payment and delivery</h2><p className="flex items-start gap-2 text-sm leading-7"><Shield size={18} className="mt-1 shrink-0 text-primary" />Seller: HireWiz, operated by {SITE.operatorName}. The displayed INR total is locked in the order summary before hosted checkout. Payment credentials are entered only with the processor. Both balances are delivered after verified captured payment.</p><div className="flex flex-wrap gap-4 text-sm font-semibold text-primary">{[["/terms","Terms"],["/privacy","Privacy"],["/refund","Refunds"],["/digital-delivery","Digital delivery"],["/contact","Support"]].map(([href,label]) => <Link key={href} href={href}>{label}</Link>)}</div></GlassCard>
  </main>;
}
