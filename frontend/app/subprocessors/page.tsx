import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Service Providers & External Processing",
  description: "External service-provider roles used or supported by the HireWiz application and the data involved.",
  alternates: { canonical: "/subprocessors" },
};

type Row = {
  provider: string;
  whenUsed: string;
  purpose: string;
  data: string;
  region: string;
};

const PROVIDER_ROLES: Row[] = [
  {
    provider: "Vercel",
    whenUsed: "Website hosting",
    purpose: "Serve the HireWiz frontend and static assets",
    data: "Standard web-request, device, network, and security-log data",
    region: "Vercel edge/cloud infrastructure",
  },
  {
    provider: "Google Cloud (Cloud Run, Cloud Tasks, and Cloud Logging)",
    whenUsed: "Backend hosting",
    purpose: "Run the HireWiz API, private analysis worker, task dispatch, and operational logs",
    data: "Application requests and data needed to perform the requested feature",
    region: "Configured Google Cloud deployment region",
  },
  {
    provider: "Production database host configured by HireWiz",
    whenUsed: "Account and application storage",
    purpose: "Store account, profile, resume text, match history, entitlement, and transaction records",
    data: "The account and service data described in the Privacy Policy",
    region: "Configured production database region",
  },
  {
    provider: "Configured AI API provider",
    whenUsed: "When you request an AI-assisted feature",
    purpose: "Resume parsing, text comparison, skill extraction, summaries, and suggestions",
    data: "Relevant resume text, job-description text, prompts, and contextual output needed for the request",
    region: "Provider infrastructure; may be outside India",
  },
  {
    provider: "TheirStack, Adzuna, or Jooble",
    whenUsed: "Only when the corresponding job-data API is configured for a market request",
    purpose: "Return a sample of job postings for market skill-demand analysis",
    data: "Role, location, country, experience, work-mode, recency, and result-count search parameters; resume content is not intended to be sent",
    region: "Provider infrastructure",
  },
  {
    provider: "Google",
    whenUsed: "Only if you choose Google sign-in",
    purpose: "Authenticate your Google account and return basic account information",
    data: "Google account identifier, name, and email made available by the sign-in flow",
    region: "Google infrastructure",
  },
  {
    provider: "Google Analytics",
    whenUsed: "Only when configured and after you accept analytics",
    purpose: "Aggregate website-usage measurement",
    data: "Page/usage events and browser, device, network, and approximate-location information",
    region: "Google infrastructure",
  },
  {
    provider: "PostHog",
    whenUsed: "Only when configured and after you accept analytics",
    purpose: "Product funnels, retention, cohorts, and rollout measurement",
    data: "Minimized product events, route, device, acquisition, and internal user identifier; no resume or evidence text",
    region: "Configured PostHog cloud region",
  },
  {
    provider: "Sentry",
    whenUsed: "When operational error reporting is configured",
    purpose: "Detect frontend, API, and worker errors and release regressions",
    data: "Minimized error, trace, release, route, and request-correlation metadata with default PII disabled",
    region: "Configured Sentry project region",
  },
  {
    provider: "Resend",
    whenUsed: "Only when lifecycle email delivery is configured",
    purpose: "Deliver onboarding, completed-analysis, reminder, and payment-confirmation messages",
    data: "Email address, message type, and the minimum account or transaction references needed for the message",
    region: "Resend infrastructure",
  },
  {
    provider: "Razorpay",
    whenUsed: "When you start the enabled India checkout",
    purpose: "Hosted payment entry, payment confirmation, fraud screening, refunds, and disputes",
    data: "Customer and transaction data needed for payment; raw payment credentials remain with the processor",
    region: "Processor and banking/network infrastructure",
  },
];

export default function SubprocessorsPage() {
  return (
    <main className="w-full max-w-[72rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-8">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Transparency"
        title="Service Providers & External Processing"
        subtitle={`Effective and last updated: ${SITE.policyEffectiveDate}`}
      />

      <FadeIn delay={0.1}>
        <GlassCard className="p-6 md:p-8 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <p className="m-0">
            HireWiz uses external infrastructure and APIs to provide the functions below. Some integrations are
            configurable and are used only when production credentials are enabled or you initiate that feature. We do
            not present an inactive payment provider as available. The provider used for a market request is also shown
            with the result.
          </p>
          <p className="text-sm text-slate-400 m-0">
            Payment card details, CVV, UPI PINs, and bank-login credentials are entered with the checkout processor and
            are not received or stored by HireWiz.
          </p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.15}>
        <GlassCard className="p-0 overflow-hidden" hoverEffect={false}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-700/60 text-[11px] uppercase tracking-wider text-slate-400">
                  <th className="px-4 py-3 font-black">Provider / role</th>
                  <th className="px-4 py-3 font-black">When used</th>
                  <th className="px-4 py-3 font-black">Purpose</th>
                  <th className="px-4 py-3 font-black">Data involved</th>
                  <th className="px-4 py-3 font-black">Processing region</th>
                </tr>
              </thead>
              <tbody>
                {PROVIDER_ROLES.map((row) => (
                  <tr key={row.provider} className="border-b border-slate-800/60 last:border-0 align-top">
                    <td className="px-4 py-4 font-bold text-white">{row.provider}</td>
                    <td className="px-4 py-4 text-slate-300">{row.whenUsed}</td>
                    <td className="px-4 py-4 text-slate-300">{row.purpose}</td>
                    <td className="px-4 py-4 text-slate-400">{row.data}</td>
                    <td className="px-4 py-4 text-slate-400">{row.region}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.2}>
        <GlassCard className="p-6 md:p-8 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <p className="text-sm text-slate-400 m-0">
            This page is updated when the production provider configuration changes. See the{" "}
            <Link href="/privacy" className="text-primary hover:underline">Privacy Policy</Link> for purposes,
            retention, choices, and rights. Questions may be sent to{" "}
            <a href={`mailto:${SITE.supportEmail}`} className="text-primary hover:underline">{SITE.supportEmail}</a>.
          </p>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
