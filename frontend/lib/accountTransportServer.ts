import { NextRequest, NextResponse } from "next/server";
import { candidateIngressHeaders } from "./candidateIngressServer";
import { getToken } from "next-auth/jwt";
import { AUTH_BODY_LIMIT, candidateAuthSecret, csrfMatches, websiteOrigin } from "./candidateAuthServer";
import { boundedPairingBytes } from "./browserPairingGateway";

export const runtime = "nodejs";
const HEADERS = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };
const PROFILE_BODY_LIMIT = 65536;
const ACCOUNT_REPLY_LIMIT = 65536;
const ACCOUNT_EXPORT_LIMIT = 16777216;
const PROFILE_FIELDS = ["full_name", "headline", "phone", "location", "linkedin", "github", "portfolio", "target_role",
  "preferred_job_type", "preferred_location", "years_experience", "bio", "skills", "education", "certifications"];
const PROFILE_REPLY = [...PROFILE_FIELDS, "email", "profile_completeness", "missing_fields", "tier", "ai_credits", "premium_until"];
const ME_REPLY = ["id", "email", "tier", "ai_credits", "job_service_credits"];
const OPERATIONS: Record<string, { path: string; methods: string[] }> = {
  register: { path: "register", methods: ["POST"] }, profile: { path: "profile", methods: ["GET", "PUT"] },
  me: { path: "me", methods: ["GET"] }, export: { path: "export-account", methods: ["GET"] },
  delete: { path: "delete-account", methods: ["POST"] },
};
const PRIVATE_FIELDS = new Set(["access_token", "accesstoken", "refresh_token", "browser_pairing_session", "browserpairingsession",
  "candidate_lifetime", "candidatelogoutrequest", "password_hash"]);
function object(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function exactFields(value: Record<string, unknown>, allowed: string[]) { return Object.keys(value).every((key) => allowed.includes(key)); }
function noAuthority(value: unknown) {
  const pending = [value]; let nodes = 0;
  while (pending.length) {
    if (++nodes > 100000) return false;
    const entry = pending.pop();
    if (!entry || typeof entry !== "object") continue;
    for (const [key, child] of Object.entries(entry)) { if (PRIVATE_FIELDS.has(key.toLowerCase())) return false; pending.push(child); }
  }
  return true;
}
function failed(status = 503) { return NextResponse.json({ detail: status === 429 ? "Too many account requests. Please wait before trying again."
  : status === 409 ? "Account already exists or sign-out is required." : "Account request is unavailable." }, { status, headers: HEADERS }); }
export const SAFE_ACCOUNT_ALIASES: Record<string, string> = {
  profile: "profile", me: "me", "export-account": "export", "delete-account": "delete",
};
export async function handleAccount(request: NextRequest, operation: string, legacyAlias = false) {
  const policy = Object.hasOwn(OPERATIONS, operation) ? OPERATIONS[operation] : undefined;
  const canonical = legacyAlias && policy ? `/api/backend/auth/${policy.path}` : `/api/account/${operation}`;
  if (!policy || request.nextUrl.search || request.nextUrl.pathname !== canonical) return failed(404);
  if (!policy.methods.includes(request.method)) return failed(405);
  const mutation = request.method !== "GET";
  if (mutation && (request.headers.get("origin") !== websiteOrigin(request)
      || request.headers.get("sec-fetch-site") === "cross-site"
      || !csrfMatches(request, request.headers.get("x-hirewiz-account-csrf") || ""))) return failed(403);
  try {
    const token = await getToken({ req: request, secret: candidateAuthSecret() });
    if (operation === "register" && token) return failed(409);
    if (operation !== "register" && typeof token?.accessToken !== "string") return failed(401);
    let raw: Buffer | undefined;
    if (mutation) {
      if (request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")) return failed(415);
      raw = await boundedPairingBytes(request.body, operation === "profile" ? PROFILE_BODY_LIMIT : AUTH_BODY_LIMIT);
      const input: unknown = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(raw));
      const allowed = operation === "profile" ? PROFILE_FIELDS : operation === "register"
        ? ["email", "password", "accepted_terms", "confirmed_age_18"] : [];
      if (!object(input) || !exactFields(input, allowed)) return failed(422);
    }
    const base = process.env.BACKEND_URL?.trim().replace(/\/+$/, "").replace(/\/api$/, "") || (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
    if (!base) return failed();
    const response = await fetch(`${base}/api/auth/${policy.path}`, { method: request.method, redirect: "error", cache: "no-store",
      signal: AbortSignal.timeout(20000), headers: candidateIngressHeaders(`/api/auth/${policy.path}`, { method: request.method, body: raw ? new Uint8Array(raw).buffer : undefined, headers: { "Content-Type": "application/json",
        ...(operation !== "register" ? { Authorization: `Bearer ${token!.accessToken}` } : {}) } }), body: raw ? new Uint8Array(raw).buffer : undefined });
    if (!response.ok) return failed([401, 409, 422, 429].includes(response.status) ? response.status : 503);
    const bytes = await boundedPairingBytes(response.body, operation === "export" ? ACCOUNT_EXPORT_LIMIT : ACCOUNT_REPLY_LIMIT);
    const data: unknown = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(bytes));
    if (!object(data) || !noAuthority(data)) return failed();
    if (operation === "register") {
      if (!exactFields(data, ME_REPLY) || !Number.isSafeInteger(data.id) || (data.id as number) <= 0) return failed();
      return NextResponse.json({ status: "REGISTERED" }, { headers: HEADERS });
    }
    if (operation === "delete") return Object.keys(data).join(",") === "status" && data.status === "deleted"
      ? NextResponse.json(data, { headers: HEADERS }) : failed();
    if (operation === "profile" && (!exactFields(data, PROFILE_REPLY) || typeof data.email !== "string")) return failed();
    if (operation === "me" && (!exactFields(data, ME_REPLY) || !Number.isSafeInteger(data.id))) return failed();
    if (operation === "export") return new NextResponse(bytes, { headers: { ...HEADERS, "Content-Type": "application/json",
      "Content-Disposition": 'attachment; filename="hirewiz-account-export.json"' } });
    return NextResponse.json(data, { headers: HEADERS });
  } catch { return failed(); }
}
