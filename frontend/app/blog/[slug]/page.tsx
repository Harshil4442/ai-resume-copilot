import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ArrowRight } from "lucide-react";

import GlassCard from "../../../components/ui/GlassCard";
import PageHeader from "../../../components/ui/PageHeader";
import TrackEventOnView from "../../../components/TrackEventOnView";
import { blogPosts, getBlogPost } from "../../../lib/seoContent";

type BlogPostPageProps = {
  params: {
    slug: string;
  };
};

export function generateStaticParams() {
  return blogPosts.map((post) => ({ slug: post.slug }));
}

export function generateMetadata({ params }: BlogPostPageProps): Metadata {
  const post = getBlogPost(params.slug);
  if (!post) return {};

  return {
    title: post.title,
    description: post.description,
    alternates: { canonical: `/blog/${post.slug}` },
  };
}

export default function BlogPostPage({ params }: BlogPostPageProps) {
  const post = getBlogPost(params.slug);
  if (!post) notFound();

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

      <GlassCard className="p-7 bg-gradient-to-r from-slate-950/70 to-blue-950/40" hoverEffect={false}>
        <h2 className="text-xl font-black text-white">Want to apply this to your own resume?</h2>
        <p className="mt-2 text-sm text-slate-300 leading-relaxed">
          Use the free public tools or create a HireWiz account to parse your resume, compare it with job-description text,
          and review learning suggestions. Results are informational and require your judgment.
        </p>
        <div className="mt-5 flex flex-col sm:flex-row gap-3">
          <Link
            href="/tools"
            className="inline-flex items-center justify-center rounded-xl border border-slate-700 px-5 py-3 text-sm font-bold text-slate-200 hover:bg-slate-800"
          >
            Try free tools
          </Link>
          <Link
            href="/register"
            className="inline-flex items-center justify-center rounded-xl bg-primary px-5 py-3 text-sm font-bold text-white hover:bg-primary/90"
          >
            Create free account <ArrowRight size={15} className="ml-2" />
          </Link>
        </div>
      </GlassCard>
    </main>
  );
}
