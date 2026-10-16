import type { NextRequest } from "next/server";

export function isTrustedRequestOrigin(request: NextRequest, requireOrigin = false): boolean {
  if (request.headers.get("sec-fetch-site")?.toLowerCase() === "cross-site") return false;
  const origin = request.headers.get("origin");
  if (!origin && requireOrigin) return false;

  try {
    // Next's route URL can contain its internal localhost fetch hostname. Host
    // represents the incoming destination; do not substitute X-Forwarded-Host.
    const host = request.headers.get("host");
    const targetOrigin = host ? new URL(`${request.nextUrl.protocol}//${host}`).origin : request.nextUrl.origin;
    if (origin) {
      const parsed = new URL(origin);
      return ["http:", "https:"].includes(parsed.protocol)
        && parsed.origin === origin
        && parsed.origin === targetOrigin;
    }

    const referer = request.headers.get("referer");
    if (referer) {
      const parsed = new URL(referer);
      return ["http:", "https:"].includes(parsed.protocol) && parsed.origin === targetOrigin;
    }
    // The BFF also supports non-browser server clients without browser headers.
    // Cookie-setting consent actions instead require a browser Origin explicitly.
    return true;
  } catch {
    return false;
  }
}
