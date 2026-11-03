import NextAuth from "next-auth";
import { authOptions } from "../../../../lib/authOptions";
import { NextRequest, NextResponse } from "next/server";
import { guardAuthRequest, websiteOrigin } from "../../../../lib/candidateAuthServer";
import { SIGN_IN_RETRY_AFTER_SECONDS, SignInRateLimitedError } from "../../../../lib/signInRateLimit";

const handler = NextAuth(authOptions);

type Context = { params: Promise<{ nextauth: string[] }> };
async function guarded(request: NextRequest, context: Context) {
  const path = (await context.params).nextauth;
  const timed = path.length === 2 && path[1] === "google"
    && ((request.method === "POST" && path[0] === "signin")
      || (request.method === "GET" && path[0] === "callback"));
  const started = timed ? performance.now() : 0;
  let status = 500;
  try {
    const checked = await guardAuthRequest(request, path[0], path[1]);
    let rateLimited = false;
    const jwt = authOptions.callbacks?.jwt;
    // NextAuth maps OAuth JWT failures to the generic Callback code. Keep only
    // our finite capacity signal in this invocation; never share it across requests.
    const requestHandler = path.length === 2 && path[0] === "callback" && path[1] === "google"
      && request.method === "GET" && jwt ? NextAuth({ ...authOptions, callbacks: {
        ...authOptions.callbacks,
        async jwt(args) {
          try { return await jwt(args); }
          catch (error) {
            if (error instanceof SignInRateLimitedError) rateLimited = true;
            throw error;
          }
        },
      } }) : handler;
    let response = checked.response ?? await requestHandler(checked.request!, context);
    if (rateLimited) {
      // Retain NextAuth's OAuth cookie cleanup, with a fixed same-origin target.
      response = new NextResponse(null, { status: 302, headers: response.headers });
      response.headers.set("Location", `${websiteOrigin(request)}/login?error=RATE_LIMITED`);
      response.headers.set("Retry-After", String(SIGN_IN_RETRY_AFTER_SECONDS));
      response.headers.set("Cache-Control", "private, no-store");
    }
    status = response.status;
    return response;
  } finally {
    if (timed) {
      // Fixed labels and monotonic duration only. OAuth URLs, cookies, proofs,
      // request identifiers and exception text must never reach these logs.
      try {
        const elapsed = performance.now() - started;
        console.info("hirewiz_oauth_route_timing_v1", {
          action: path[0], provider: "google",
          elapsed_ms: Number.isFinite(elapsed) ? Math.min(600_000, Math.max(0, Math.round(elapsed))) : 0,
          status,
        });
      } catch { /* Diagnostics cannot change an authentication outcome. */ }
    }
  }
}
export { guarded as GET, guarded as POST };
