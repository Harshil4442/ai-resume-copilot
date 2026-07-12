import type { MetadataRoute } from "next";
import { blogPosts, toolPages } from "../lib/seoContent";

const BASE_URL = "https://www.hirewizhq.com";

// Only public, canonical pages belong in the sitemap.
const PUBLIC_ROUTES = [
  "",
  "/pricing",
  "/about",
  "/digital-delivery",
  "/subprocessors",
  "/login",
  "/register",
  "/terms",
  "/privacy",
  "/refund",
  "/cookies",
  "/contact",
  "/tools",
  "/blog",
  "/resources",
];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date("2026-07-11T00:00:00.000Z");
  const dynamicRoutes = [
    ...toolPages.map((tool) => `/tools/${tool.slug}`),
    ...blogPosts.map((post) => `/blog/${post.slug}`),
  ];

  return [...PUBLIC_ROUTES, ...dynamicRoutes].map((route) => ({
    url: `${BASE_URL}${route}`,
    lastModified,
    changeFrequency: route.startsWith("/blog") ? "weekly" : "monthly",
    priority: route === "" ? 1 : route.startsWith("/tools") ? 0.85 : route.startsWith("/blog") ? 0.8 : 0.7,
  }));
}
