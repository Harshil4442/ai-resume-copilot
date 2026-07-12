import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, BookOpen } from "lucide-react";

import GlassCard from "../../components/ui/GlassCard";
import PageHeader from "../../components/ui/PageHeader";
import TrackEventOnView from "../../components/TrackEventOnView";
import { blogPosts } from "../../lib/seoContent";

export const metadata: Metadata = {
  title: "Resume Guides",
  description:
    "Practical resume guides for keywords, job-description matching, freshers, software engineers, React developers, Java developers, and data analysts.",
  alternates: { canonical: "/blog" },
};

export default function BlogIndexPage() {
  return (
    <main className="w-full max-w-[76rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView eventName="blog_index_viewed" />
      <PageHeader
        badge="Resume guides"
        title="Career-document guides for people improving their own resume."
        subtitle="Short, practical articles that help visitors learn before creating a free HireWiz account."
      />

      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {blogPosts.map((post) => (
          <GlassCard key={post.slug} className="p-7 flex flex-col" hoverEffect={false}>
            <div className="w-10 h-10 rounded-xl bg-purple-500/15 text-purple-300 flex items-center justify-center mb-5">
              <BookOpen size={20} />
            </div>
            <div className="text-xs font-bold uppercase tracking-wider text-purple-300">
              {post.category} · {post.readTime}
            </div>
            <h2 className="mt-2 text-xl font-black text-white">{post.title}</h2>
            <p className="mt-3 text-sm text-slate-400 leading-relaxed flex-1">{post.description}</p>
            <Link
              href={`/blog/${post.slug}`}
              className="mt-6 inline-flex items-center gap-2 text-sm font-bold text-primary hover:text-blue-300"
            >
              Read guide <ArrowRight size={15} />
            </Link>
          </GlassCard>
        ))}
      </section>
    </main>
  );
}
