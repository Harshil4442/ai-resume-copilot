import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ArrowRight, CheckCircle2 } from "lucide-react";

import BulletOptimizer from "../../../components/BulletOptimizer";
import GlassCard from "../../../components/ui/GlassCard";
import PageHeader from "../../../components/ui/PageHeader";
import TrackEventOnView from "../../../components/TrackEventOnView";
import { getToolPage, toolPages } from "../../../lib/seoContent";

type ToolPageProps = {
  params: {
    slug: string;
  };
};

export function generateStaticParams() {
  return toolPages.map((tool) => ({ slug: tool.slug }));
}

export function generateMetadata({ params }: ToolPageProps): Metadata {
  const tool = getToolPage(params.slug);
  if (!tool) return {};

  return {
    title: tool.metaTitle,
    description: tool.description,
    alternates: { canonical: `/tools/${tool.slug}` },
  };
}

export default function ToolPage({ params }: ToolPageProps) {
  const tool = getToolPage(params.slug);
  if (!tool) notFound();

  return (
    <main className="w-full max-w-[76rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView
        eventName="public_tool_viewed"
        params={{ tool_slug: tool.slug, tool_title: tool.title }}
      />

      <Link href="/tools" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary">
        <ArrowLeft size={16} className="mr-2" /> All tools
      </Link>

      <PageHeader badge={tool.badge} title={tool.title} subtitle={tool.intro} />

      {tool.embedsBulletOptimizer ? (
        <GlassCard className="p-2 md:p-4" hoverEffect={false}>
          <BulletOptimizer />
        </GlassCard>
      ) : null}

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <GlassCard className="p-7" hoverEffect={false}>
          <h2 className="text-xl font-black text-white">How to use it</h2>
          <ol className="mt-5 space-y-4">
            {tool.steps.map((step, index) => (
              <li key={step} className="flex gap-3 text-sm text-slate-300 leading-relaxed">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-black text-blue-300">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </GlassCard>

        <GlassCard className="p-7" hoverEffect={false}>
          <h2 className="text-xl font-black text-white">Review checklist</h2>
          <ul className="mt-5 space-y-3">
            {tool.checklist.map((item) => (
              <li key={item} className="flex items-start gap-2 text-sm text-slate-300 leading-relaxed">
                <CheckCircle2 size={16} className="text-emerald-400 mt-0.5 shrink-0" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </GlassCard>
      </section>

      <GlassCard className="p-7 bg-gradient-to-r from-blue-950/50 to-slate-950/50" hoverEffect={false}>
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-5">
          <div>
            <h2 className="text-xl font-black text-white">Go deeper with a free account</h2>
            <p className="mt-2 text-sm text-slate-300 leading-relaxed">{tool.cta}</p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 shrink-0">
            <Link
              href="/register"
              className="inline-flex items-center justify-center rounded-xl bg-primary px-5 py-3 text-sm font-bold text-white hover:bg-primary/90"
            >
              Sign up free <ArrowRight size={15} className="ml-2" />
            </Link>
            <Link
              href="/pricing"
              className="inline-flex items-center justify-center rounded-xl border border-slate-700 px-5 py-3 text-sm font-bold text-slate-200 hover:bg-slate-800"
            >
              View Premium
            </Link>
          </div>
        </div>
      </GlassCard>
    </main>
  );
}
