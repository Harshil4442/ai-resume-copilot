import type { MetadataRoute } from "next";

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
];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date("2026-07-11T00:00:00.000Z");
  return PUBLIC_ROUTES.map((route) => ({
    url: `${BASE_URL}${route}`,
    lastModified,
    changeFrequency: "monthly",
    priority: route === "" ? 1 : 0.7,
  }));
}
