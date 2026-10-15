/** Private server transport freshness/replay, never a browser capability. */
import { createHmac, randomUUID } from "node:crypto";

const PROTOCOL = "hirewiz:candidate-auth-private_v1";
const OPERATIONS = new Set([
  "GET /api/auth/candidate/v1/availability", "GET /api/auth/candidate/v1/session",
  "POST /api/auth/candidate/v1/register", "POST /api/auth/candidate/v1/login",
  "POST /api/auth/candidate/v1/registration-status", "POST /api/auth/candidate/v1/logout",
  "POST /api/auth/candidate/v1/web-logout", "POST /api/auth/candidate/v1/password",
  "POST /api/auth/register", "POST /api/auth/login", "POST /api/auth/delete-account",
]);
const fail = () => { throw new Error("AUTH_UNAVAILABLE"); };
const domain = (purpose: string) => Buffer.from(`${PROTOCOL}:${purpose}\0`, "ascii");
function digest(key: Buffer, purpose: string, raw: Buffer) {
  const derived = createHmac("sha256", key).update(`${PROTOCOL}:key\0`, "ascii").update(domain(purpose)).digest();
  return createHmac("sha256", derived).update(domain(purpose)).update(raw).digest();
}
function bytes(body?: BodyInit | null) {
  if (body == null) return Buffer.alloc(0);
  if (typeof body === "string") return Buffer.from(body, "utf8");
  if (body instanceof ArrayBuffer) return Buffer.from(body);
  if (body instanceof Uint8Array) return Buffer.from(body);
  return fail();
}
export function candidateIngressHeaders(path: string, init: RequestInit = {}, now = Date.now(), requestId = randomUUID()) {
  const method = init.method || "GET";
  const headers = new Headers(init.headers);
  headers.delete("x-hirewiz-candidate-ingress");
  if (!OPERATIONS.has(`${method} ${path}`)) {
    if (path.startsWith("/api/auth/candidate/")) return fail();
    return headers;
  }
  const configured = process.env.CANDIDATE_AUTH_TRANSPORT_SECRET?.trim();
  const keyId = process.env.CANDIDATE_AUTH_TRANSPORT_KEY_ID?.trim();
  // Ordinary unenrolled legacy auth remains available without provision. A
  // mapped account/native endpoint will fail closed at its backend branch.
  if (!configured && !keyId && !path.startsWith("/api/auth/candidate/")) return headers;
  if (!configured || !/^[a-f0-9]{64}$/.test(configured) || !keyId || !/^[a-z][a-z0-9_-]{0,63}$/.test(keyId)
      || configured === process.env.BROWSER_PAIRING_GATEWAY_SECRET?.trim()
      || configured === process.env.NEXTAUTH_SECRET?.trim()) return fail();
  const website = process.env.NEXTAUTH_URL;
  if (!website) return fail();
  const url = new URL(website);
  if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return fail();
  if (!Number.isSafeInteger(now) || now <= 0 || now > Number.MAX_SAFE_INTEGER - 5000 || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(requestId)) return fail();
  const body = bytes(init.body), authorization = Buffer.from(headers.get("authorization") || "", "utf8");
  if (body.length > 8192 || authorization.length > 8192) return fail();
  const key = Buffer.from(configured, "hex"), prefix = Buffer.from(`${requestId}\0`, "ascii");
  const assertion = {
    version: 1, issuer: "hirewiz_candidate_bff_v1", audience: PROTOCOL, key_id: keyId,
    request_id: requestId, method, path, origin: url.origin,
    issued_at_ms: now, expires_at_ms: now + 5000,
    body_commitment: digest(key, "body", Buffer.concat([prefix, body])).toString("hex"),
    authorization_commitment: digest(key, "authorization", Buffer.concat([prefix, authorization])).toString("hex"),
  };
  const sorted = Object.fromEntries(Object.entries(assertion).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0));
  const raw = Buffer.from(JSON.stringify(sorted), "utf8");
  headers.set("x-hirewiz-candidate-ingress", `${raw.toString("base64url")}.${digest(key, "assertion", raw).toString("base64url")}`);
  return headers;
}
