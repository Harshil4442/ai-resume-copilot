import { createHash } from "node:crypto";
import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { candidateAuthSecret, csrfMatches, websiteOrigin } from "./candidateAuthServer";
import { boundedPairingBytes } from "./browserPairingGateway";
import { BROWSER_BACKEND_TIMEOUT_MS } from "./browserTransportTimeouts";
import { submissionReviewBillingMatches } from "./browserSubmissionBilling";

const CANDIDATE = new Set(["device", "confirm", "revoke", "review", "revise", "approve", "cancel"]);
const DEVICE = new Set(["prepare", "status", "application", "inspect", "begin", "outcome", "cancel", "artifact", "reconcile"]);
const HEADERS = { "Cache-Control": "private, no-store", Pragma: "no-cache", "X-Content-Type-Options": "nosniff" };
const ARTIFACT_TYPES = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);
const MAX_ARTIFACT_BYTES = 10 * 1024 * 1024;
function failure(status = 503) {
  return NextResponse.json({ detail: "Reviewed browser assistance is unavailable. Check the original request status; no automatic retry will occur." }, { status, headers: HEADERS });
}

function publicReply(value: unknown): boolean {
  if (Array.isArray(value)) return value.every(publicReply);
  if (!value || typeof value !== "object") return true;
  const record = value as Record<string, unknown>;
  // A malformed upstream success must never turn a server credential into a
  // client response. Public EC coordinates remain allowed; private JWKs do not.
  const credentials = new Set(["accesstoken", "refreshtoken", "idtoken", "authorization", "privatekey", "clientsecret", "signingsecret", "password", "apikey"]);
  return !Object.keys(record).some((key) => credentials.has(key.replace(/[_-]/g, "").toLowerCase()))
    && !("kty" in record && ("d" in record || "p" in record || "q" in record))
    && Object.values(record).every(publicReply);
}

async function artifactReply(response: Response) {
  const length = response.headers.get("content-length") || "";
  const mediaType = response.headers.get("content-type") || "";
  const sha256 = response.headers.get("x-artifact-sha256") || "";
  const filename = response.headers.get("x-artifact-filename") || "";
  if (response.status !== 200 || response.redirected || response.headers.has("content-encoding") || response.headers.has("content-range")
    || !/^[1-9][0-9]{0,7}$/.test(length) || Number(length) > MAX_ARTIFACT_BYTES || !ARTIFACT_TYPES.has(mediaType)
    || !/^[a-f0-9]{64}$/.test(sha256) || !/^[\x20-\x7e]{1,240}$/.test(filename) || !filename.trim()) {
    try { void response.body?.cancel().catch(() => undefined); } catch { /* preserve refusal */ }
    return failure();
  }
  const raw = await boundedPairingBytes(response.body, Number(length));
  if (raw.byteLength !== Number(length) || createHash("sha256").update(raw).digest("hex") !== sha256) return failure();
  return new NextResponse(new Uint8Array(raw), { headers: { ...HEADERS, "Content-Type": mediaType,
    "Content-Length": length, "X-Artifact-SHA256": sha256, "X-Artifact-Filename": filename } });
}

export async function browserAssistanceGateway(request: NextRequest, operation: string, device: boolean) {
  try {
    if (!(device ? DEVICE : CANDIDATE).has(operation) || request.nextUrl.search
      || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")
      || request.headers.has("authorization")) return failure(403);
    const origin = request.headers.get("origin") || "";
    if (device ? !/^chrome-extension:\/\/[a-p]{32}$/.test(origin) || request.headers.has("cookie")
      : origin !== websiteOrigin(request) || request.headers.get("sec-fetch-site") === "cross-site"
        || !csrfMatches(request, request.headers.get("x-hirewiz-account-csrf") || "")) return failure(403);
    const token = device ? null : await getToken({ req: request, secret: candidateAuthSecret() });
    if (!device && typeof token?.accessToken !== "string") return failure(401);
    const base = process.env.BACKEND_URL?.trim().replace(/\/+$/, "").replace(/\/api$/, "")
      || (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "");
    if (!base) return failure();
    const bytes = await boundedPairingBytes(request.body, device ? 65536 : operation === "revise" ? 16384 : 4096);
    const input: unknown = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(bytes));
    if (!input || typeof input !== "object" || Array.isArray(input)) return failure(422);
    if (!device && operation === "approve" && "accepted_service_quote_id" in input) {
      const approval = input as Record<string, unknown>;
      if (Object.keys(approval).sort().join(",") !== "accepted_service_quote_id,confirmed,identity,review_digest"
        || approval.confirmed !== true || typeof approval.identity !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(approval.identity)
        || typeof approval.review_digest !== "string" || !/^[a-f0-9]{64}$/.test(approval.review_digest)
        || typeof approval.accepted_service_quote_id !== "string" || !/^csq_[a-f0-9]{32}$/.test(approval.accepted_service_quote_id)) return failure(422);
    }
    const response = await fetch(`${base}/api/v1/browser-assistance/${device ? "device" : "candidate"}/${operation}`, {
      method: "POST", credentials: "omit", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(BROWSER_BACKEND_TIMEOUT_MS),
      headers: { "Content-Type": "application/json", ...(device ? { Origin: origin } : { Authorization: `Bearer ${token!.accessToken}` }) },
      body: new Uint8Array(bytes),
    });
    if (!response.ok) {
      try { void response.body?.cancel().catch(() => undefined); } catch { /* keep the original outcome */ }
      return failure([401, 403, 404, 409, 410, 413, 422, 429].includes(response.status) ? response.status : 503);
    }
    if (device && operation === "artifact") return await artifactReply(response);
    const raw = await boundedPairingBytes(response.body, 65536);
    const result: unknown = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(raw));
    if (!result || typeof result !== "object" || Array.isArray(result) || !publicReply(result)) return failure();
    const reply = result as Record<string, unknown>;
    if ((!device && ["review", "approve", "cancel", "revise"].includes(operation) || device && ["inspect", "outcome", "reconcile"].includes(operation))
      && !submissionReviewBillingMatches(reply)) return failure();
    if (device && operation === "status" && reply.review != null && !submissionReviewBillingMatches(reply.review)) return failure();
    return NextResponse.json(result, { headers: HEADERS });
  } catch { return failure(); }
}
