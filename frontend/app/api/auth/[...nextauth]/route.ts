import NextAuth from "next-auth";
import { authOptions } from "../../../../lib/authOptions";
import { NextRequest } from "next/server";
import { guardAuthRequest } from "../../../../lib/candidateAuthServer";

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
    const response = checked.response ?? await handler(checked.request!, context);
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
