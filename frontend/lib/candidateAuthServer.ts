import { createHash, timingSafeEqual } from "node:crypto";
import { candidateIngressHeaders } from "./candidateIngressServer";
import type { JWT } from "next-auth/jwt";
import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { boundedPairingBytes, retainedBrowserSession } from "./browserPairingGateway";

export const AUTH_BODY_LIMIT = 8192;
export const AUTH_RESPONSE_LIMIT = 16384;
const HEADERS = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };

export function candidateAuthSecret() {
  return process.env.NEXTAUTH_SECRET?.trim() || "hirewiz-local-development-secret-change-me";
}

export function websiteOrigin(request: Request) {
  const configured = process.env.NEXTAUTH_URL;
  const origin = new URL(configured || request.url).origin;
  if (process.env.NODE_ENV === "production" && (!configured || !origin.startsWith("https://"))) throw new Error("AUTH_UNAVAILABLE");
  return origin;
}

export function authFailure(request: Request, status: number, code: string) {
  return NextResponse.json({ url: `${websiteOrigin(request)}/login?error=${code}` }, { status, headers: HEADERS });
}

export async function backendAuth(path: string, init: RequestInit = {}) {
  const base = (process.env.BACKEND_URL || "http://localhost:8000").replace(/\/+$/, "").replace(/\/api$/, "");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${base}/api/auth/${path}`, { ...init, headers: candidateIngressHeaders(`/api/auth/${path}`, init), redirect: "error", cache: "no-store",
      signal: controller.signal });
    const bytes = await boundedPairingBytes(response.body, AUTH_RESPONSE_LIMIT);
    const value: unknown = JSON.parse(bytes.toString("utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("AUTH_UNAVAILABLE");
    return { status: response.status, ok: response.ok, data: value as Record<string, unknown> };
  } finally { clearTimeout(timer); }
}

export function csrfMatches(request: NextRequest, token: string) {
  const secret = candidateAuthSecret();
  const cookie = request.cookies.get("__Host-next-auth.csrf-token")?.value
    ?? request.cookies.get("next-auth.csrf-token")?.value;
  if (!cookie || !/^[a-f0-9]{64}$/.test(token)) return false;
  const parts = cookie.split("|");
  if (parts.length !== 2 || parts[0] !== token || !/^[a-f0-9]{64}$/.test(parts[1])) return false;
  const expected = createHash("sha256").update(token + secret).digest();
  return timingSafeEqual(expected, Buffer.from(parts[1], "hex"));
}

export async function guardAuthRequest(request: NextRequest, action: string, provider?: string) {
  const token = await getToken({ req: request, secret: candidateAuthSecret() });
  const retained = retainedBrowserSession(token);
  if (token?.browserPairingSession != null && !retained) return { response: authFailure(request, 503, "AUTH_UNAVAILABLE") };
  if (retained && ["signin", "callback"].includes(action)) {
    return { response: authFailure(request, 409, "SESSION_LOGOUT_REQUIRED") };
  }
  // OAuth GET callbacks keep their existing parser. Retained replacement was
  // blocked above, independently of any provider/email claim.
  if (request.method !== "POST" || !["signout", "signin", "callback"].includes(action)
      || (action === "callback" && provider !== "credentials")) return { request };
  if (request.headers.get("origin") !== websiteOrigin(request) || request.nextUrl.search) {
    return { response: authFailure(request, 403, "AUTH_REQUEST_REJECTED") };
  }
  try {
    if (request.headers.get("content-type")?.split(";")[0] !== "application/x-www-form-urlencoded"
        || request.headers.has("content-encoding")) throw new Error("AUTH_REQUEST_REJECTED");
    const raw = await boundedPairingBytes(request.body, AUTH_BODY_LIMIT);
    const form = new URLSearchParams(new TextDecoder("utf8", { fatal: true }).decode(raw));
    const allowed = new Set(["csrfToken", "callbackUrl", "json", ...(provider === "credentials" ? ["email", "password", "redirect"] : [])]);
    for (const key of form.keys()) if (!allowed.has(key) || form.getAll(key).length !== 1) throw new Error("AUTH_REQUEST_REJECTED");
    if (form.has("redirect") && !["true", "false"].includes(form.get("redirect")!)) throw new Error("AUTH_REQUEST_REJECTED");
    if (!csrfMatches(request, form.get("csrfToken") || "")) throw new Error("AUTH_REQUEST_REJECTED");
    if (action === "signout" && retained) {
      const result = await revokeRetainedToken(token);
      if (!result) return { response: authFailure(request, 503, "LOGOUT_PENDING") };
    }
    return { request: new NextRequest(request.url, { method: request.method, headers: request.headers, body: raw }) };
  } catch { return { response: authFailure(request, 503, action === "signout" ? "LOGOUT_PENDING" : "AUTH_REQUEST_REJECTED") }; }
}

export async function revokeRetainedToken(token: JWT | null) {
  const context = retainedBrowserSession(token);
  if (!context || typeof token?.accessToken !== "string" || token.hirewizUserId !== context.candidate_id) return false;
  if (typeof token.candidateLogoutRequest !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(token.candidateLogoutRequest)) return false;
  const result = await backendAuth("candidate/v1/web-logout", { method: "POST",
    headers: { Authorization: `Bearer ${token.accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ operation_id: token.candidateLogoutRequest }) });
  return result.ok && Object.keys(result.data).join(",") === "status" && result.data.status === "COOKIE_DENIAL_RETAINED";
}
