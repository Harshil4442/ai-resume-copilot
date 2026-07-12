import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, CheckCircle2, FileText, Search, Target, Wand2 } from "lucide-react";

import GlassCard from "../../components/ui/GlassCard";
import PageHeader from "../../components/ui/PageHeader";
import TrackEventOnView from "../../components/TrackEventOnView";
import { toolPages } from "../../lib/seoContent";

export const metadata: Metadata = {
  title: "Free Resume Tools",
  description:
    "Free public resume tools and checklists for resume bullets, resume scoring, keyword scanning, and job-description matching.",
  alternates: { canonical: "/tools" },
};

const icons = [Wand2, FileText, Target, Search];

export default function ToolsIndexPage() {
  return (
    <main className="w-full max-w-[76rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView eventName="public_tools_index_viewed" />
      <PageHeader
        badge="Free tools"
        title="Public resume tools before you sign up."
        subtitle="Use quick checklists and a public bullet optimizer to review your resume. Create a free account when you want deeper analysis."
      />

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {toolPages.map((tool, index) => {
          const Icon = icons[index % icons.length];
          return (
            <GlassCard key={tool.slug} className="p-7 flex flex-col" hoverEffect={false}>
              <div className="w-11 h-11 rounded-xl bg-blue-500/15 text-blue-300 flex items-center justify-center mb-5">
                <Icon size={22} />
              </div>
              <div className="text-xs font-bold uppercase tracking-wider text-blue-300">{tool.badge}</div>
              <h2 className="mt-2 text-2xl font-black text-white">{tool.title}</h2>
              <p className="mt-3 text-sm text-slate-400 leading-relaxed flex-1">{tool.description}</p>
              <Link
                href={`/tools/${tool.slug}`}
                className="mt-6 inline-flex items-center gap-2 text-sm font-bold text-primary hover:text-blue-300"
              >
                Open tool <ArrowRight size={15} />
              </Link>
            </GlassCard>
          );
        })}
      </section>

      <GlassCard className="p-7 bg-slate-950/50" hoverEffect={false}>
        <h2 className="text-xl font-black text-white">What these tools are — and are not</h2>
        <ul className="mt-4 grid gap-3 text-sm text-slate-300 md:grid-cols-2">
          {[
            "They provide informational checks and writing suggestions.",
            "They do not guarantee ATS acceptance, interviews, offers, or employment.",
            "They work best when you verify every suggestion against your real experience.",
            "They are not recruitment, staffing, placement, or employer-candidate matching services.",
          ].map((item) => (
            <li key={item} className="flex items-start gap-2">
              <CheckCircle2 size={16} className="text-emerald-400 mt-0.5 shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </GlassCard>
    </main>
  );
}
