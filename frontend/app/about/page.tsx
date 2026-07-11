import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { ArrowLeft, FileText, Target, TrendingUp, BookOpen, ShieldCheck, AlertTriangle } from "lucide-react";

export const metadata = {
  title: "About & How It Works — HireWiz",
  description:
    "HireWiz is self-service, AI-assisted resume-analysis software. What it does, what it does not do, and how the AI is meant to be used.",
};

const STEPS = [
  { icon: FileText, title: "You upload your own resume", text: "Upload your resume (and optionally a job description). We parse it into structured, reviewable sections." },
  { icon: Target, title: "We estimate compatibility", text: "Our AI compares your resume with the job description and produces a HireWiz compatibility estimate and likely skill gaps." },
  { icon: TrendingUp, title: "We show market signals", text: "We analyze a sample of recent postings from third-party job-data providers to estimate which skills are frequently requested." },
  { icon: BookOpen, title: "You decide what to do", text: "You get learning suggestions and project ideas. Every AI suggestion is informational and requires your review before you use it." },
];

export default function AboutPage() {
  return (
    <main className="w-full max-w-[56rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="About & How It Works"
        title="AI-assisted resume analysis you review and control."
        subtitle="HireWiz is self-service software operated in India by SAVALIYA HARSHIL YOGESHBHAI, an individual trading as HireWiz."
      />

      <FadeIn delay={0.1}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <p>
            HireWiz is an automated, self-service software platform. You upload your own resume and, optionally, a job
            description, and receive AI-assisted resume parsing, compatibility estimates, skill-gap analysis, market
            insights, and learning suggestions. You stay in control of every change.
          </p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.15}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          {STEPS.map((s, i) => (
            <GlassCard key={i} className="p-6 md:p-8" hoverEffect={false}>
              <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center text-primary mb-4">
                <s.icon size={20} />
              </div>
              <h3 className="text-lg font-black text-white mb-2">{s.title}</h3>
              <p className="text-sm text-slate-400 leading-relaxed">{s.text}</p>
            </GlassCard>
          ))}
        </div>
      </FadeIn>

      <FadeIn delay={0.2}>
        <GlassCard className="p-8 md:p-10" hoverEffect={false}>
          <div className="flex items-center gap-3 mb-5">
            <AlertTriangle size={22} className="text-amber-500" />
            <h2 className="text-xl font-black text-white tracking-tight">What HireWiz is not</h2>
          </div>
          <ul className="space-y-3 text-sm text-slate-300">
            <li className="flex items-start gap-2"><span className="text-amber-500 font-black mt-0.5">•</span> HireWiz is <strong>not</strong> a recruitment agency, staffing company, job-placement service, job board, or automated job-application service.</li>
            <li className="flex items-start gap-2"><span className="text-amber-500 font-black mt-0.5">•</span> We do <strong>not</strong> make employment decisions, submit applications on your behalf, or recruit or place candidates.</li>
            <li className="flex items-start gap-2"><span className="text-amber-500 font-black mt-0.5">•</span> Compatibility scores are <strong>HireWiz estimates</strong>, not scores produced by any employer or a specific applicant-tracking system (ATS). We do not guarantee ATS acceptance, interviews, offers, or employment.</li>
            <li className="flex items-start gap-2"><span className="text-amber-500 font-black mt-0.5">•</span> The service must <strong>not</strong> be used for impersonation, false credentials, another person's resume without authorization, exam or hiring cheating, or fabricated achievements.</li>
          </ul>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.25}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-4" hoverEffect={false}>
          <div className="flex items-center gap-3 mb-2">
            <ShieldCheck size={22} className="text-emerald-400" />
            <h2 className="text-xl font-black text-white tracking-tight">How to use the AI safely</h2>
          </div>
          <p>
            AI-generated suggestions can be inaccurate and may occasionally produce incorrect or fabricated details. You
            are responsible for reviewing and verifying any wording, skills, or achievements before adding them to your
            resume or sharing them. The AI must not invent employers, dates, degrees, certifications, or metrics.
          </p>
          <p className="text-sm text-slate-400">
            Market insights are informational estimates based on the specific sample of job postings analyzed. Coverage
            depends on our third-party job-data providers and may not represent the entire market. See our{" "}
            <Link href="/subprocessors" className="text-primary hover:underline">list of service providers</Link>.
          </p>
          <div className="pt-4 border-t border-slate-700/60 flex flex-wrap gap-x-4 gap-y-2 text-xs font-semibold text-slate-400">
            <Link href="/pricing" className="hover:text-primary">Pricing</Link>
            <Link href="/terms" className="hover:text-primary">Terms of Service</Link>
            <Link href="/privacy" className="hover:text-primary">Privacy Policy</Link>
            <Link href="/contact" className="hover:text-primary">Contact</Link>
          </div>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
