/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // Vercel: set BACKEND_URL to your Cloud Run base URL (no trailing slash)
    // Example: https://ai-resume-parser-xxxxxx-uc.a.run.app
    let backend = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "";

    // If someone accidentally sets ".../api", strip it to avoid double "/api/api"
    backend = backend.replace(/\/+$/, "");
    if (backend.endsWith("/api")) backend = backend.slice(0, -4);

    // If not set (e.g., local build without backend), skip rewrites.
    // NOTE: your app will not be able to call the API until BACKEND_URL is set.
    if (!backend) return [];

    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;