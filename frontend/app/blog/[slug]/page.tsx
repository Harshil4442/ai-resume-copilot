import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ArrowRight, CheckCircle2 } from "lucide-react";

import GlassCard from "../../../components/ui/GlassCard";
import PageHeader from "../../../components/ui/PageHeader";
import TrackEventOnView from "../../../components/TrackEventOnView";
import TrackedInternalLink from "../../../components/TrackedInternalLink";
import { blogPosts, getBlogPost } from "../../../lib/seoContent";

type BlogPostPageProps = {
  params: Promise<{
    slug: string;
  }>;
};

export function generateStaticParams() {
  return blogPosts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({ params }: BlogPostPageProps): Promise<Metadata> {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) return {};

  return {
    title: post.title,
    description: post.description,
    alternates: { canonical: `/blog/${post.slug}` },
  };
}

function relatedToolForPost(slug: string) {
  if (slug.includes("match")) {
    return {
      href: "/tools/job-description-match-checker",
      label: "Open JD match checker",
      title: "Apply this with the JD match checker",
    };
  }
  if (slug.includes("keyword") || slug.includes("developer") || slug.includes("analyst")) {
    return {
      href: "/tools/resume-keyword-scanner",
      label: "Open keyword scanner",
      title: "Apply this with the keyword scanner",
    };
  }
  if (slug.includes("format")) {
    return {
      href: "/tools/resume-score-checker",
      label: "Open resume score checklist",
      title: "Apply this with the resume score checklist",
    };
  }
  return {
    href: "/tools/resume-bullet-optimizer",
    label: "Open bullet optimizer",
    title: "Apply this with the bullet optimizer",
  };
}

export default async function BlogPostPage({ params }: BlogPostPageProps) {
  const { slug } = await params;
  const post = getBlogPost(slug);
  if (!post) notFound();
  const relatedTool = relatedToolForPost(post.slug);

  return (
    <main className="w-full max-w-[58rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView
        eventName="blog_post_viewed"
        params={{ post_slug: post.slug, post_title: post.title, category: post.category }}
      />

      <Link href="/blog" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary">
        <ArrowLeft size={16} className="mr-2" /> All guides
      </Link>

      <PageHeader
        badge={`${post.category} · ${post.readTime}`}
        title={post.title}
        subtitle={post.description}
      />

      <GlassCard className="p-6 border-blue-800/70 bg-blue-950/20" hoverEffect={false}>
        <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-xs font-bold uppercase tracking-wider text-blue-300">Interactive next step</div>
            <h2 className="mt-1 text-xl font-black text-white">{relatedTool.title}</h2>
            <p className="mt-2 text-sm text-slate-300 leading-relaxed">
              Read the guide, then use the related public tool to turn the advice into a concrete resume review step.
            </p>
          </div>
          <TrackedInternalLink
            href={relatedTool.href}
            eventName="blog_related_tool_clicked"
            eventParams={{ post_slug: post.slug, related_tool: relatedTool.href }}
            className="inline-flex shrink-0 items-center justify-center rounded-xl bg-primary px-5 py-3 text-sm font-bold text-white hover:bg-primary/90"
          >
            {relatedTool.label} <ArrowRight size={15} className="ml-2" />
          </TrackedInternalLink>
        </div>
      </GlassCard>

      <article className="space-y-6">
        {post.sections.map((section) => (
          <GlassCard key={section.heading} className="p-7 md:p-9" hoverEffect={false}>
            <h2 className="text-2xl font-black text-white">{section.heading}</h2>
            <div className="mt-4 space-y-4 text-slate-300 leading-relaxed">
              {section.body.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
            </div>
          </GlassCard>
        ))}
      </article>

      <GlassCard className="p-7" hoverEffect={false}>
        <h2 className="text-xl font-black text-white">What to do after reading</h2>
        <ul className="mt-5 grid gap-3 text-sm text-slate-300 md:grid-cols-2">
          {[
            "Pick one target role or job description.",
            "Map the guide advice to truthful resume evidence.",
            "Use the related public tool for a quick check.",
            "Create a free account when you want full resume parsing and comparison.",
          ].map((item) => (
            <li key={item} className="flex items-start gap-2">
              <CheckCircle2 size={16} className="text-emerald-400 mt-0.5 shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </GlassCard>

      <GlassCard className="p-7 bg-gradient-to-r from-slate-950/70 to-blue-950/40" hoverEffect={false}>
        <h2 className="text-xl font-black text-white">Want to apply this to your own resume?</h2>
        <p className="mt-2 text-sm text-slate-300 leading-relaxed">
          Use the free public tools or create a HireWiz account to parse your resume, compare it with job-description text,
          and review learning suggestions. Results are informational and require your judgment.
        </p>
        <div className="mt-5 flex flex-col sm:flex-row gap-3">
          <TrackedInternalLink
            href="/tools"
            eventName="blog_tools_cta_clicked"
            eventParams={{ post_slug: post.slug }}
            className="inline-flex items-center justify-center rounded-xl border border-slate-700 px-5 py-3 text-sm font-bold text-slate-200 hover:bg-slate-800"
          >
            Try free tools
          </TrackedInternalLink>
          <TrackedInternalLink
            href="/register"
            eventName="blog_signup_cta_clicked"
            eventParams={{ post_slug: post.slug }}
            className="inline-flex items-center justify-center rounded-xl bg-primary px-5 py-3 text-sm font-bold text-white hover:bg-primary/90"
          >
            Create free account <ArrowRight size={15} className="ml-2" />
          </TrackedInternalLink>
        </div>
      </GlassCard>
    </main>
  );
}
