import type { Metadata } from "next";

import BlogExplorer from "../../components/BlogExplorer";
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
        title="Searchable resume guides, connected to tools."
        subtitle="Find a guide by role or skill, then use the related public tool to apply the advice before creating a free account."
      />

      <BlogExplorer posts={blogPosts} />
    </main>
  );
}
