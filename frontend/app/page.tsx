import {
  ArrowRight,
  BarChart3,
  BriefcaseBusiness,
  Check,
  FileCheck2,
  Fingerprint,
  Gauge,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

const workflow = [
  { label: "Evidence", detail: "Approve the facts that represent your work.", icon: Fingerprint },
  { label: "Opportunity", detail: "Capture the role and preserve its original snapshot.", icon: BriefcaseBusiness },
  { label: "Application", detail: "Connect the exact resume, preparation, and follow-up.", icon: FileCheck2 },
  { label: "Outcome", detail: "Learn from each response, interview, and offer.", icon: BarChart3 },
];

const differentiators = [
  {
    title: "Claims stay traceable",
    copy: "Every important resume claim can point back to evidence you approved. Missing facts become questions, not inventions.",
    icon: ShieldCheck,
  },
  {
    title: "Every role gets context",
    copy: "The job snapshot, match, resume version, interview work, contacts, reminders, and outcome remain together.",
    icon: BriefcaseBusiness,
  },
  {
    title: "Learning earns its place",
    copy: "Skill ROI ranks the next learning action by demand across your actual target roles and the evidence you already have.",
    icon: Gauge,
  },
];

export default function HomePage() {
  return (
    <main className="w-full">
      <section className="home-hero relative flex min-h-[620px] items-end overflow-hidden border-b border-white/10 bg-[#111513]">
        <div className="absolute inset-0 bg-[url('/workspace-preview.png')] bg-cover bg-center opacity-[0.58] lg:opacity-[0.8]" aria-hidden="true" />
        <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(8,12,10,0.99)_0%,rgba(8,12,10,0.94)_42%,rgba(8,12,10,0.34)_72%,rgba(8,12,10,0.08)_100%),linear-gradient(0deg,#0f1211_0%,transparent_42%)]" aria-hidden="true" />
        <div className="page-container relative z-10 pb-16 pt-24 sm:pb-20">
          <div className="max-w-2xl">
            <p className="eyebrow">Evidence-backed career workspace</p>
            <h1 className="mt-4 text-5xl font-black leading-[1.02] text-[#f4f2ea] sm:text-6xl lg:text-7xl">HireWiz</h1>
            <p className="mt-5 max-w-xl text-lg leading-8 text-neutral-300 sm:text-xl">
              Turn real career evidence into stronger role decisions, tailored resumes, interview preparation, and a job search that remembers what happened.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link href="/register" className="button-primary min-w-40">
                Build your workspace <ArrowRight size={16} aria-hidden="true" />
              </Link>
              <Link href="/pricing" className="button-secondary min-w-32">View pricing</Link>
            </div>
            <div className="mt-8 flex flex-wrap gap-x-6 gap-y-3 text-sm text-neutral-400">
              {[
                "50 free analysis units",
                "No automatic renewal",
                "You approve every claim",
              ].map((item) => (
                <span key={item} className="flex items-center gap-2">
                  <Check size={15} className="text-primary" aria-hidden="true" /> {item}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-white/10 bg-[#f0c96b] py-5 text-[#18140a]">
        <div className="page-container flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="font-black">One workspace from saved role to final outcome.</p>
          <Link href="/about" className="inline-flex items-center gap-2 text-sm font-bold hover:underline">
            See the product approach <ArrowRight size={15} />
          </Link>
        </div>
      </section>

      <section className="py-20 sm:py-24">
        <div className="page-container">
          <div className="max-w-2xl">
            <p className="eyebrow">A connected job search</p>
            <h2 className="mt-3 text-3xl font-black leading-tight text-[#f4f2ea] sm:text-4xl">
              Stop rebuilding context for every application.
            </h2>
            <p className="muted-copy mt-4 text-base">
              HireWiz keeps the decisions and source material attached to the role, so the next action starts from what you already know.
            </p>
          </div>
          <ol className="mt-12 grid border-y border-white/10 md:grid-cols-4">
            {workflow.map((step, index) => (
              <li key={step.label} className="min-w-0 border-b border-white/10 px-0 py-7 md:border-b-0 md:border-r md:px-6 first:md:pl-0 last:border-0">
                <div className="flex items-center justify-between">
                  <step.icon size={21} className="text-primary" aria-hidden="true" />
                  <span className="text-xs font-bold text-neutral-600">0{index + 1}</span>
                </div>
                <h3 className="mt-6 text-lg font-black text-neutral-100">{step.label}</h3>
                <p className="mt-2 text-sm leading-6 text-neutral-500">{step.detail}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="border-y border-white/10 bg-[#141716] py-20 sm:py-24">
        <div className="page-container grid gap-12 lg:grid-cols-[0.82fr_1.18fr] lg:items-start">
          <div className="lg:sticky lg:top-28">
            <p className="eyebrow">Built around trust</p>
            <h2 className="mt-3 text-3xl font-black leading-tight sm:text-4xl">AI should work from your history, not rewrite it.</h2>
            <p className="muted-copy mt-4">
              Your approved evidence is the boundary. HireWiz can select, organize, and sharpen it for a role while keeping you in control.
            </p>
            <Link href="/register" className="button-primary mt-7">
              Start with your evidence <Sparkles size={16} aria-hidden="true" />
            </Link>
          </div>
          <div className="divide-y divide-white/10 border-y border-white/10">
            {differentiators.map((item) => (
              <article key={item.title} className="grid gap-4 py-8 sm:grid-cols-[44px_1fr]">
                <span className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <item.icon size={20} aria-hidden="true" />
                </span>
                <div>
                  <h3 className="text-xl font-black text-neutral-100">{item.title}</h3>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-500">{item.copy}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="py-20 sm:py-24">
        <div className="page-container grid gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <p className="eyebrow">Start free, upgrade when it matters</p>
            <h2 className="mt-3 text-3xl font-black leading-tight sm:text-4xl">A focused 30-day Premium pass. No renewal surprise.</h2>
            <p className="muted-copy mt-4 max-w-xl">
              Use the free allowance to build context and test a match. Premium removes analysis-unit deductions for 30 days while your search is active.
            </p>
          </div>
          <div className="border-l-2 border-accent pl-6 sm:pl-8">
            <p className="data-label">HireWiz Premium</p>
            <p className="mt-2 text-5xl font-black text-accent">INR 999</p>
            <p className="mt-2 text-sm text-neutral-500">One-time payment for 30 days. No automatic renewal.</p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Link href="/pricing" className="button-secondary">Review full terms</Link>
              <Link href="/register" className="button-primary">Create free account</Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
