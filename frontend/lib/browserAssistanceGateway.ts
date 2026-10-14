import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { candidateAuthSecret, csrfMatches, websiteOrigin } from "./candidateAuthServer";
import { boundedPairingBytes } from "./browserPairingGateway";

const CANDIDATE = new Set(["device", "confirm", "revoke", "review", "approve", "cancel"]);
const DEVICE = new Set(["prepare", "status", "application", "inspect", "begin", "outcome", "cancel"]);
const HEADERS = { "Cache-Control": "private, no-store", Pragma: "no-cache", "X-Content-Type-Options": "nosniff" };
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
    const bytes = await boundedPairingBytes(request.body, device ? 65536 : 4096);
    const input: unknown = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(bytes));
    if (!input || typeof input !== "object" || Array.isArray(input)) return failure(422);
    const response = await fetch(`${base}/api/v1/browser-assistance/${device ? "device" : "candidate"}/${operation}`, {
      method: "POST", credentials: "omit", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(12000),
      headers: { "Content-Type": "application/json", ...(device ? { Origin: origin } : { Authorization: `Bearer ${token!.accessToken}` }) },
      body: new Uint8Array(bytes),
    });
    if (!response.ok) {
      try { void response.body?.cancel().catch(() => undefined); } catch { /* keep the original outcome */ }
      return failure([401, 403, 404, 409, 410, 413, 422, 429].includes(response.status) ? response.status : 503);
    }
    const raw = await boundedPairingBytes(response.body, 65536);
    const result: unknown = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(raw));
    if (!result || typeof result !== "object" || Array.isArray(result) || !publicReply(result)) return failure();
    return NextResponse.json(result, { headers: HEADERS });
  } catch { return failure(); }
}
