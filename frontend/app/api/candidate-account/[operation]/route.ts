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
    if (!retained) return NextResponse.json({ status: token.browserPairingSession != null ? "UNAVAILABLE" : token.candidateSignInMethod === "google" ? "GOOGLE_ENROLLMENT_AVAILABLE" : "LEGACY_ENROLLMENT_UNAVAILABLE" }, { headers: HEADERS });
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
        || typeof reply.data.fresh_registration !== "boolean" || typeof reply.data.legacy_enrollment !== "boolean") throw new Error("Invalid reply");
    return NextResponse.json(reply.data, { headers: HEADERS });
  } catch { return NextResponse.json({ fresh_registration: false, legacy_enrollment: false }, { headers: HEADERS }); }
}

export async function POST(request: NextRequest, context: Context) {
  const operation = (await context.params).operation;
  if (!["register", "registration-status", "google-enrollment-challenge", "enroll-google", "enroll-password", "recovery-google-challenge", "recover-google"].includes(operation)) return new NextResponse(null, { status: 404 });
  const recovery = ["recovery-google-challenge", "recover-google"].includes(operation);
  if (request.headers.get("origin") !== websiteOrigin(request) || !csrfMatches(request, request.headers.get("x-hirewiz-account-csrf") || "") || request.nextUrl.search
      || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")) {
    return NextResponse.json({ status: "REJECTED" }, { status: 403, headers: HEADERS });
  }
  try {
    const headers = new Headers({ "Content-Type": "application/json" });
    let expectedCandidate: number | null = null;
    if (["google-enrollment-challenge", "enroll-google", "enroll-password"].includes(operation) || recovery) {
      const token = await getToken({ req: request, secret: candidateAuthSecret() });
      if (!token || typeof token.accessToken !== "string" || !Number.isSafeInteger(token.hirewizUserId) || Number(token.hirewizUserId) <= 0) return NextResponse.json({ status: recovery ? "RECOVERY_AUTHENTICATION_FAILED" : "AUTHENTICATION_FAILED" }, { status: 401, headers: HEADERS });
      if (token.browserPairingSession != null) return NextResponse.json({ status: recovery ? "RECOVERY_UNAVAILABLE" : "ALREADY_ENROLLED" }, { status: 409, headers: HEADERS });
      if (recovery && token.candidateSignInMethod !== "google") return NextResponse.json({ status: "RECOVERY_AUTHENTICATION_FAILED" }, { status: 401, headers: HEADERS });
      headers.set("Authorization", `Bearer ${token.accessToken}`);
      expectedCandidate = Number(token.hirewizUserId);
    }
    if (operation === "register") {
      const token = await getToken({ req: request, secret: candidateAuthSecret() });
      if (token?.browserPairingSession != null) return NextResponse.json({ status: "SESSION_LOGOUT_REQUIRED" }, { status: 409, headers: HEADERS });
    }
    const raw = await boundedPairingBytes(request.body, AUTH_BODY_LIMIT);
    const reply = await backendAuth(`candidate/v1/${operation}`, { method: "POST", headers, body: raw });
    if (reply.status === 429) return NextResponse.json({ status: "RATE_LIMITED" }, { status: 429, headers: HEADERS });
    if (recovery && !reply.ok) {
      const refusals: Record<string, number> = { RECOVERY_AUTHENTICATION_FAILED: 401, RECOVERY_EMAIL_PROOF_REQUIRED: 403, RECOVERY_UNAVAILABLE: 409 };
      if (Object.keys(reply.data).join(",") === "status" && typeof reply.data.status === "string" && refusals[reply.data.status] === reply.status) {
        return NextResponse.json({ status: reply.data.status }, { status: reply.status, headers: HEADERS });
      }
      if (reply.status === 422) return NextResponse.json({ status: "RECOVERY_REQUEST_REJECTED" }, { status: 422, headers: HEADERS });
      // An untyped failure may follow the PENDING commit. Preserve uncertainty;
      // only the exact pre-commit refusal contract authorizes another review.
      return NextResponse.json({ status: "UNAVAILABLE" }, { status: 503, headers: HEADERS });
    }
    if (!reply.ok) return NextResponse.json({ status: reply.status === 401 ? "AUTHENTICATION_FAILED" : "UNAVAILABLE" }, { status: reply.status === 401 ? 401 : 503, headers: HEADERS });
    if (["register", "enroll-google", "enroll-password", "recover-google"].includes(operation) && reply.data.status === "ENROLLED"
        && typeof reply.data.user_id === "number" && Number.isSafeInteger(reply.data.user_id) && reply.data.user_id > 0
        && (expectedCandidate === null || reply.data.user_id === expectedCandidate)
        && Object.keys(reply.data).sort().join(",") === "status,user_id") {
      return NextResponse.json({ status: "ENROLLED" }, { headers: HEADERS });
    }
    if (["google-enrollment-challenge", "recovery-google-challenge"].includes(operation) && Object.keys(reply.data).sort().join(",") === "challenge_id,client_id,expires_at_ms,nonce"
        && typeof reply.data.challenge_id === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(reply.data.challenge_id)
        && typeof reply.data.nonce === "string" && /^[A-Za-z0-9_-]{32,128}$/.test(reply.data.nonce)
        && typeof reply.data.client_id === "string" && reply.data.client_id.length > 0 && reply.data.client_id.length <= 512
        && typeof reply.data.expires_at_ms === "number" && Number.isSafeInteger(reply.data.expires_at_ms)
        && Date.now() < reply.data.expires_at_ms && reply.data.expires_at_ms <= Date.now() + 305000) {
      return NextResponse.json(reply.data, { headers: HEADERS });
    }
    if (operation === "registration-status" && Object.keys(reply.data).join(",") === "status"
        && ["ENROLLED", "PENDING_REVIEW_REQUIRED", "LEGACY_ENROLLMENT_UNAVAILABLE"].includes(reply.data.status as string)) {
      return NextResponse.json(reply.data, { headers: HEADERS });
    }
    throw new Error("Invalid reply");
  } catch { return NextResponse.json({ status: "UNAVAILABLE" }, { status: 503, headers: HEADERS }); }
}
