import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export const metadata = {
  title: "Service Providers (Subprocessors) — HireWiz",
  description:
    "The third-party service providers HireWiz uses to operate the platform, the data they process, and where.",
};

type Row = {
  provider: string;
  purpose: string;
  data: string;
  region: string;
};

const SUBPROCESSORS: Row[] = [
  {
    provider: "Google (Gemini AI)",
    purpose: "AI resume parsing, compatibility estimates, skill extraction, and suggestions",
    data: "Resume text and job-description text you submit for analysis",
    region: "Google Cloud (global)",
  },
  {
    provider: "Adzuna",
    purpose: "Job-posting data used for market skill-demand analysis",
    data: "Your search parameters (role, location, filters); no resume content",
    region: "Provider infrastructure",
  },
  {
    provider: "Jooble",
    purpose: "Job-posting data used for market skill-demand analysis",
    data: "Your search parameters (role, location, filters); no resume content",
    region: "Provider infrastructure",
  },
  {
    provider: "TheirStack",
    purpose: "Job-posting data used for market skill-demand analysis",
    data: "Your search parameters (role, location, filters); no resume content",
    region: "Provider infrastructure",
  },
  {
    provider: "Google Cloud Run",
    purpose: "Hosting and running the HireWiz backend application",
    data: "All application data in transit while requests are processed",
    region: "Google Cloud",
  },
  {
    provider: "Neon (PostgreSQL database)",
    purpose: "Primary database storing your account, profile, resumes, and history",
    data: "Account, profile, resume, and match data",
    region: "Neon cloud",
  },
  {
    provider: "Vercel",
    purpose: "Hosting and serving the HireWiz website (frontend)",
    data: "Standard web request/log data",
    region: "Vercel edge/cloud",
  },
  {
    provider: "Google OAuth",
    purpose: "Optional “Sign in with Google” authentication",
    data: "Your Google account identifier and email (only if you use Google sign-in)",
    region: "Google",
  },
  {
    provider: "Google Analytics 4",
    purpose: "Aggregate website usage measurement to improve the product",
    data: "Usage events, approximate location, device/browser information",
    region: "Google",
  },
  {
    provider: "PayPal",
    purpose: "Payment processing for international (USD) checkout, where enabled",
    data: "Payment and transaction metadata (payment credentials are handled by the provider, not HireWiz)",
    region: "PayPal",
  },
];

export default function SubprocessorsPage() {
  return (
    <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-8">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Transparency"
        title="Service Providers (Subprocessors)"
        subtitle="Last updated: 11 July 2026"
      />

      <FadeIn delay={0.1}>
        <GlassCard className="p-6 md:p-8 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <p className="m-0">
            To operate HireWiz, we rely on the third-party service providers below. We share only the data needed for
            each provider's function, and we do not sell your personal data. Payment card details are collected and
            handled by the payment provider at checkout — HireWiz does not receive or store raw card, UPI, or bank
            credentials.
          </p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.15}>
        <GlassCard className="p-0 overflow-hidden" hoverEffect={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-700/60 text-[11px] uppercase tracking-wider text-slate-400">
                  <th className="px-4 py-3 font-black">Provider</th>
                  <th className="px-4 py-3 font-black">Purpose</th>
                  <th className="px-4 py-3 font-black">Data processed</th>
                  <th className="px-4 py-3 font-black">Region</th>
                </tr>
              </thead>
              <tbody>
                {SUBPROCESSORS.map((row, i) => (
                  <tr key={i} className="border-b border-slate-800/60 last:border-0 align-top">
                    <td className="px-4 py-4 font-bold text-white whitespace-nowrap">{row.provider}</td>
                    <td className="px-4 py-4 text-slate-300">{row.purpose}</td>
                    <td className="px-4 py-4 text-slate-400">{row.data}</td>
                    <td className="px-4 py-4 text-slate-400 whitespace-nowrap">{row.region}</td>
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
            We update this list as our providers change. Questions about how your data is handled? See our{" "}
            <Link href="/privacy" className="text-primary hover:underline">Privacy Policy</Link> or email{" "}
            <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>.
          </p>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
