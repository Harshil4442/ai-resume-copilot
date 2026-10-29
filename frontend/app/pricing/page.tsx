import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, Shield } from "lucide-react";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import TrackEventOnView from "../../components/TrackEventOnView";
import { getPublicUnifiedCreditCatalog } from "../../lib/unifiedCreditCatalog";
import PublicUnifiedCreditPacks from "../../components/billing/PublicUnifiedCreditPacks";
import { SITE } from "../../lib/site";

export const dynamic = "force-dynamic";
const description = "Review HireWiz pilot availability and verified INR prices for eligible career services. Credit passes have no automatic renewal when purchases are open.";
export const metadata: Metadata = {
  title: "Pricing",
  description,
  alternates: { canonical: "/pricing" },
  openGraph: {
    type: "website",
    url: "/pricing",
    siteName: SITE.name,
    title: "Pricing | HireWiz",
    description,
    images: [{ url: "/images/hirewiz-share.png", width: 1200, height: 630, alt: "HireWiz — Evidence-backed career workspace" }],
  },
};

export default async function PricingPage() {
  const result = await getPublicUnifiedCreditCatalog();
  const catalog = result.catalog;
  const purchasesOpen = catalog?.checkout_enabled === true;
  return <main className="mx-auto w-full max-w-[72rem] space-y-10 px-4 py-12 sm:px-6 md:px-8">
    <TrackEventOnView eventName="pricing_viewed" />
    <Link href="/" className="inline-flex items-center text-sm font-semibold text-muted-foreground hover:text-primary"><ArrowLeft size={16} className="mr-2" /> Back to Home</Link>
    <PageHeader badge="Public pricing" title="A clear plan for your next move." subtitle={purchasesOpen
      ? "One credit balance. Three finite 30-day passes. One-time INR payments for customers in India, with no automatic renewal."
      : catalog ? "Review the current credit-pass preview. Purchases are not open during this pilot."
        : "Review the limited pilot. Credit passes and checkout are not available on this page right now."} />
    <p className="text-sm leading-7 text-muted-foreground"><strong className="text-foreground">Pilot coverage:</strong> discovery is limited to 20 reviewed employer origins, with source availability shown in Employer Jobs. Candidate-reviewed browser assistance supports Razorpay’s Greenhouse portal; other employers use a manual handoff. Review current availability and the exact supported action before spending credits.</p>
    <FadeIn>{catalog ? <PublicUnifiedCreditPacks catalog={catalog} /> : <GlassCard className="p-6" hoverEffect={false}>
      <h2 className="font-display text-xl">{result.status === "not_open" ? "Credit passes are not open yet" : "Current prices cannot be verified"}</h2>
      <p className="mt-2 text-sm text-muted-foreground">{result.status === "not_open"
        ? "HireWiz is in a limited pilot. This page will show reviewed packs and exact prices when purchases open. No payment can start now."
        : "Please try again later or contact support. No checkout can start without verified current prices."}</p>
    </GlassCard>}</FadeIn>
    <GlassCard className="space-y-4 p-6 sm:p-8" hoverEffect={false}><h2 className="font-display text-2xl">Choose only the services you need</h2>
      <p className="text-sm leading-7">When credit services are available, you can use them for employer discovery, enhanced resume matching, interview preparation and resume tailoring. Each service shows its exact credit charge, selected resume and job, deliverable and limits before you approve it. Original-resume applications do not require an AI purchase.</p>
      <p className="text-sm leading-7">Viewing, downloading and unchanged reuse of an existing result do not incur another generation charge. Failed or incomplete services restore credits usable for at least seven days after restoration, or receive the applicable monetary remedy. A costly request may need a separately reviewed quote; we do not reduce document quality to fit its price.</p>
      <p className="text-sm leading-7">Application capabilities are shown separately from credit availability. Candidate-reviewed assistance binds the exact employer destination, resume and answers. Filling or uploading is not a completed application. Where final submission is offered, its charge requires verified complete employer acceptance; an unknown outcome stays reserved until reconciled.</p>
      <p className="text-sm leading-7">New pass credits expire after their disclosed validity period and do not renew automatically. They are non-transferable software allowances with no cash value. Older purchases keep their original terms and appear separately in billing until an approved migration is complete.</p>
    </GlassCard>
    <GlassCard className="space-y-4 p-6 sm:p-8" hoverEffect={false}><h2 className="font-display text-2xl">Payment and delivery</h2><p className="flex items-start gap-2 text-sm leading-7"><Shield size={18} className="mt-1 shrink-0 text-primary" />Seller: HireWiz, operated by {SITE.operatorName}. Review the total INR payable, credit quantity and minimum expiry before hosted checkout. A server quote holds the price for up to 15 minutes. Payment credentials are entered only with the processor, and credits are delivered after verified captured payment.</p><div className="flex flex-wrap gap-4 text-sm font-semibold text-primary">{[["/terms","Terms"],["/privacy","Privacy"],["/refund","Refunds"],["/digital-delivery","Digital delivery"],["/contact","Support"]].map(([href,label]) => <Link key={href} href={href}>{label}</Link>)}</div></GlassCard>
  </main>;
}
