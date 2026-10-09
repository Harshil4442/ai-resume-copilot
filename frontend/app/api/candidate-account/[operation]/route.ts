import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { AUTH_BODY_LIMIT, backendAuth, candidateAuthSecret, csrfMatches, websiteOrigin } from "../../../../lib/candidateAuthServer";
import { boundedPairingBytes, retainedBrowserSession } from "../../../../lib/browserPairingGateway";

export const runtime = "nodejs";
const HEADERS = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };
type Context = { params: Promise<{ operation: string }> };

export async function GET(request: NextRequest, context: Context) {
  const operation = (await context.params).operation;
  if (!["availability", "eligibility"].includes(operation) || request.nextUrl.search) return new NextResponse(null, { status: 404 });
  if (operation === "eligibility") {
    const token = await getToken({ req: request, secret: candidateAuthSecret() });
    if (!token) return NextResponse.json({ status: "PASSWORD_SIGN_IN_REQUIRED" }, { headers: HEADERS });
    const retained = retainedBrowserSession(token);
    if (!retained) return NextResponse.json({ status: token.browserPairingSession != null ? "UNAVAILABLE" : "LEGACY_ENROLLMENT_UNAVAILABLE" }, { headers: HEADERS });
    try {
      if (typeof token.accessToken !== "string" || token.hirewizUserId !== retained.candidate_id) throw new Error("Invalid context");
      const reply = await backendAuth("candidate/v1/session", { headers: { Authorization: `Bearer ${token.accessToken}` } });
      const eligible = reply.ok && Object.keys(reply.data).join(",") === "status" && reply.data.status === "ELIGIBLE";
      return NextResponse.json({ status: eligible ? "ELIGIBLE" : reply.status === 401 ? "PASSWORD_SIGN_IN_REQUIRED" : "UNAVAILABLE" }, { headers: HEADERS });
    } catch { return NextResponse.json({ status: "UNAVAILABLE" }, { headers: HEADERS }); }
  }
  try {
    const reply = await backendAuth("candidate/v1/availability");
    if (!reply.ok || Object.keys(reply.data).sort().join(",") !== "fresh_registration,legacy_enrollment"
        || typeof reply.data.fresh_registration !== "boolean" || reply.data.legacy_enrollment !== false) throw new Error("Invalid reply");
    return NextResponse.json(reply.data, { headers: HEADERS });
  } catch { return NextResponse.json({ fresh_registration: false, legacy_enrollment: false }, { headers: HEADERS }); }
}

export async function POST(request: NextRequest, context: Context) {
  const operation = (await context.params).operation;
  if (!["register", "registration-status"].includes(operation)) return new NextResponse(null, { status: 404 });
  if (request.headers.get("origin") !== websiteOrigin(request) || !csrfMatches(request, request.headers.get("x-hirewiz-account-csrf") || "") || request.nextUrl.search
      || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")) {
    return NextResponse.json({ status: "REJECTED" }, { status: 403, headers: HEADERS });
  }
  try {
    if (operation === "register") {
      const token = await getToken({ req: request, secret: candidateAuthSecret() });
      if (token?.browserPairingSession != null) return NextResponse.json({ status: "SESSION_LOGOUT_REQUIRED" }, { status: 409, headers: HEADERS });
    }
    const raw = await boundedPairingBytes(request.body, AUTH_BODY_LIMIT);
    const reply = await backendAuth(`candidate/v1/${operation}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: raw });
    if (!reply.ok) return NextResponse.json({ status: reply.status === 401 ? "AUTHENTICATION_FAILED" : "UNAVAILABLE" }, { status: reply.status === 401 ? 401 : 503, headers: HEADERS });
    if (operation === "register" && reply.data.status === "ENROLLED"
        && typeof reply.data.user_id === "number" && Number.isSafeInteger(reply.data.user_id) && reply.data.user_id > 0
        && Object.keys(reply.data).sort().join(",") === "status,user_id") {
      return NextResponse.json({ status: "ENROLLED" }, { headers: HEADERS });
    }
    if (operation === "registration-status" && Object.keys(reply.data).join(",") === "status"
        && ["ENROLLED", "PENDING_REVIEW_REQUIRED", "LEGACY_ENROLLMENT_UNAVAILABLE"].includes(reply.data.status as string)) {
      return NextResponse.json(reply.data, { headers: HEADERS });
    }
    throw new Error("Invalid reply");
  } catch { return NextResponse.json({ status: "UNAVAILABLE" }, { status: 503, headers: HEADERS }); }
}
