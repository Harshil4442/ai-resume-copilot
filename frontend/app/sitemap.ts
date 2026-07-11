import type { MetadataRoute } from "next";

const BASE_URL = "https://www.hirewizhq.com";

// Only public, canonical pages belong in the sitemap. New public pages
// (e.g. /pricing, /about, /digital-delivery, /subprocessors) should be added
// here as they are built.
const PUBLIC_ROUTES = [
  "",
  "/pricing",
  "/about",
  "/digital-delivery",
  "/subprocessors",
  "/login",
  "/register",
  "/market",
  "/terms",
  "/privacy",
  "/refund",
  "/cookies",
  "/contact",
];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return PUBLIC_ROUTES.map((route) => ({
    url: `${BASE_URL}${route}`,
    lastModified,
    changeFrequency: "monthly",
    priority: route === "" ? 1 : 0.7,
  }));
}
