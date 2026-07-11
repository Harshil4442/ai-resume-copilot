/** @type {import('next').NextConfig} */
const configuredApiOrigins = [
  process.env.NEXT_PUBLIC_API_BASE_URL,
  process.env.BACKEND_URL,
]
  .filter(Boolean)
  .flatMap((value) => {
    try {
      return [new URL(value).origin];
    } catch {
      return [];
    }
  });

const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : ''} https://checkout.razorpay.com https://www.googletagmanager.com`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://*.razorpay.com https://www.google-analytics.com",
  "font-src 'self' data:",
  `connect-src 'self' ${configuredApiOrigins.join(' ')} https://*.razorpay.com https://www.google-analytics.com https://region1.google-analytics.com`,
  "frame-src 'self' https://*.razorpay.com",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(process.env.NODE_ENV === 'production' ? ["upgrade-insecure-requests"] : []),
]
  .join('; ')
  .replace(/\s{2,}/g, ' ');

const securityHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-DNS-Prefetch-Control', value: 'off' },
  { key: 'Cross-Origin-Opener-Policy', value: 'same-origin-allow-popups' },
  ...(process.env.NODE_ENV === 'production'
    ? [{ key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' }]
    : []),
  {
    key: 'Permissions-Policy',
    value: 'camera=(), microphone=(), geolocation=(), browsing-topics=()',
  },
];

const privateRouteHeaders = [
  { key: 'Cache-Control', value: 'private, no-store, max-age=0' },
  { key: 'Pragma', value: 'no-cache' },
  { key: 'X-Robots-Tag', value: 'noindex, nofollow, noarchive' },
];

const authRouteHeaders = privateRouteHeaders.filter(
  (header) => header.key !== 'X-Robots-Tag',
);

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
      ...[
        '/dashboard/:path*',
        '/resume/:path*',
        '/jobs/:path*',
        '/market/:path*',
        '/learning/:path*',
        '/profile/:path*',
        '/billing/:path*',
        '/logout/:path*',
        '/logo-playground/:path*',
      ].map((source) => ({ source, headers: privateRouteHeaders })),
      ...['/login', '/register'].map((source) => ({
        source,
        headers: authRouteHeaders,
      })),
    ];
  },
  async redirects() {
    return [
      {
        source: '/:path*',
        has: [
          {
            type: 'host',
            value: 'ai-resume-copilot-three.vercel.app',
          },
        ],
        destination: 'https://www.hirewizhq.com/:path*',
        permanent: true,
      },
      {
        // Canonicalize the apex domain to the www host.
        source: '/:path*',
        has: [
          {
            type: 'host',
            value: 'hirewizhq.com',
          },
        ],
        destination: 'https://www.hirewizhq.com/:path*',
        permanent: true,
      },
    ];
  },
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
        // Exclude NextAuth specific paths from being proxied to the FastAPI backend
        source: "/api/:path((?!auth/callback|auth/signin|auth/signout|auth/session|auth/providers|auth/csrf|auth/error|auth/google-consent).*)",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
