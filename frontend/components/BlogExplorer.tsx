"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowRight, BookOpen, Search } from "lucide-react";

import type { BlogPost } from "../lib/seoContent";
import { trackEvent } from "../lib/analytics";
import GlassCard from "./ui/GlassCard";

type BlogExplorerProps = {
  posts: BlogPost[];
};

export default function BlogExplorer({ posts }: BlogExplorerProps) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");

  const categories = useMemo(
    () => ["All", ...Array.from(new Set(posts.map((post) => post.category)))],
    [posts],
  );

  const filteredPosts = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return posts.filter((post) => {
      const matchesCategory = category === "All" || post.category === category;
      const searchable = `${post.title} ${post.description} ${post.category}`.toLowerCase();
      const matchesQuery = !normalizedQuery || searchable.includes(normalizedQuery);
      return matchesCategory && matchesQuery;
    });
  }, [category, posts, query]);

  return (
    <section className="space-y-6">
      <GlassCard className="p-5 md:p-6" hoverEffect={false}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
            <input
              value={query}
              onChange={(event) => {
                const value = event.target.value;
                setQuery(value);
                if (value.trim().length >= 3) {
                  trackEvent("blog_search_used", { query: value.trim().toLowerCase() });
                }
              }}
              placeholder="Search guides by role, keyword, or skill..."
              className="w-full rounded-xl border border-slate-700 bg-slate-950/60 py-3 pl-10 pr-4 text-sm font-semibold text-white placeholder:text-slate-500 outline-none focus:border-primary"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {categories.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => {
                  setCategory(item);
                  trackEvent("blog_category_filter_used", { category: item });
                }}
                className={`rounded-full px-3 py-2 text-xs font-bold transition ${
                  category === item
                    ? "bg-primary text-white"
                    : "border border-slate-700 text-slate-300 hover:bg-slate-800"
                }`}
              >
                {item}
              </button>
            ))}
          </div>
        </div>
      </GlassCard>

      <div className="text-sm font-semibold text-slate-400">
        Showing {filteredPosts.length} of {posts.length} guides
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredPosts.map((post) => (
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
              onClick={() => trackEvent("blog_card_clicked", { post_slug: post.slug, category: post.category })}
              className="mt-6 inline-flex items-center gap-2 text-sm font-bold text-primary hover:text-blue-300"
            >
              Read guide <ArrowRight size={15} />
            </Link>
          </GlassCard>
        ))}
      </div>
    </section>
  );
}
