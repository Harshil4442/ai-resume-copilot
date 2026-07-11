import type { MetadataRoute } from "next";

const BASE_URL = "https://www.hirewizhq.com";

// Public pages are crawlable; authenticated/account routes are kept out of the index.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/api/",
        "/dashboard",
        "/resume",
        "/jobs",
        "/market",
        "/learning",
        "/profile",
        "/billing",
        "/logout",
        "/logo-playground",
      ],
    },
    sitemap: `${BASE_URL}/sitemap.xml`,
    host: BASE_URL,
  };
}
