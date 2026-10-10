import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { isTrustedRequestOrigin } from "../../../../../lib/requestOrigin";
import { boundedPairingBytes, candidateGatewayHeaders, CANDIDATE_OPERATIONS, CSRF_COOKIE,
  gatewayConfiguration, issuePairingCsrf, MAX_PAIRING_BODY, MAX_PAIRING_RESPONSE, candidateReply,
  retainedBrowserSession, verifyPairingCsrf } from "../../../../../lib/browserPairingGateway";

const responseHeaders = { "Cache-Control": "private, no-store, max-age=0", Pragma: "no-cache", "X-Content-Type-Options": "nosniff" };
function unavailable(status: number) {
  return NextResponse.json({ detail: status === 403 ? "Browser pairing request was rejected" : "Browser pairing is currently unavailable" }, { status, headers: responseHeaders });
}

export async function POST(request: NextRequest, context: { params: Promise<{ operation: string }> }) {
  try {
    if (!isTrustedRequestOrigin(request, true) || request.nextUrl.search || request.headers.get("content-type") !== "application/json"
        || request.headers.get("content-encoding") || request.headers.get("authorization")) return unavailable(403);
    const { operation } = await context.params;
    if (operation !== "csrf" && !CANDIDATE_OPERATIONS.has(operation)) return unavailable(403);
    const token = await getToken({ req: request, secret: process.env.NEXTAUTH_SECRET, secureCookie: true });
    if (!token) return unavailable(401);
    const retained = retainedBrowserSession(token);
    if (!retained) return unavailable(503);
    const configuration = gatewayConfiguration();
    if (request.headers.get("origin") !== configuration.origin) return unavailable(403);
    const raw = await boundedPairingBytes(request.body, MAX_PAIRING_BODY);
    if (operation === "csrf") {
      if (raw.toString("utf8") !== "{}") return unavailable(403);
      const csrf = issuePairingCsrf(configuration.key, retained);
      const response = NextResponse.json({ csrf_token: csrf.token }, { headers: responseHeaders });
      response.cookies.set(CSRF_COOKIE, csrf.cookie, { secure: true, httpOnly: true, sameSite: "strict", path: "/", maxAge: 600 });
      return response;
    }
    if (!verifyPairingCsrf(configuration.key, retained, request.cookies.get(CSRF_COOKIE)?.value,
                          request.headers.get("x-hirewiz-csrf"))) return unavailable(403);
    const path = `/api/v1/browser-pairing/candidate/${operation}`;
    const backend = await fetch(`${configuration.backend}${path}`, { method: "POST",
      credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(12000),
      headers: candidateGatewayHeaders(configuration.key, configuration.origin, retained, path, raw), body: new Uint8Array(raw) });
    if (!backend.ok) {
      // Arbitrary cancellation acknowledgements cannot delay or remap this error.
      try { void backend.body?.cancel().catch(() => undefined); } catch { /* best effort */ }
      return unavailable(backend.status === 403 ? 403 : 503);
    }
    const body = await boundedPairingBytes(backend.body, MAX_PAIRING_RESPONSE);
    return NextResponse.json(candidateReply(operation, JSON.parse(body.toString("utf8"))), { headers: responseHeaders });
  } catch {
    // Never log authentication cookies, passwords, request bodies or gateway assertions.
    return unavailable(503);
  }
}

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
