/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Production (Vercel): set BACKEND_URL to your Cloud Run base URL
    // Example: https://ai-resume-parser-xxxxx-uc.a.run.app
    // Local dev: falls back to http://localhost:8000
    let backend = process.env.BACKEND_URL || "";
    backend = backend.replace(/\/+$/, "");
    if (backend.endsWith("/api")) backend = backend.slice(0, -4);
    if (!backend) backend = "http://localhost:8000";

    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
