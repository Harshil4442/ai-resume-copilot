import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import type { JWT } from "next-auth/jwt";

export const CSRF_COOKIE = "__Host-hirewiz-pairing-csrf";
export const MAX_PAIRING_BODY = 8192;
export const MAX_PAIRING_RESPONSE = 16384;
export const CANDIDATE_OPERATIONS = new Set(["challenge", "confirm", "revoke-challenge", "revoke"]);
export const DEVICE_OPERATIONS = new Set(["prepare", "create", "challenge", "complete", "status", "refresh-challenge", "refresh"]);
const UUID_PATTERN = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

export type RetainedBrowserSession = { candidate_id: number; account_binding_id: string; session_id: string };

export function retainedBrowserSession(token: JWT | null): RetainedBrowserSession | null {
  // This future claim must be installed by reviewed native session provisioning at login.
  // General accessToken/hirewizUserId and the public client session are insufficient.
  const value = token?.browserPairingSession;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const session = value as Record<string, unknown>;
  if (Object.keys(session).sort().join(",") !== "account_binding_id,candidate_id,session_id"
      || typeof session.candidate_id !== "number" || !Number.isSafeInteger(session.candidate_id) || session.candidate_id <= 0
      || typeof session.account_binding_id !== "string" || !UUID_PATTERN.test(session.account_binding_id)
      || typeof session.session_id !== "string" || !UUID_PATTERN.test(session.session_id)) return null;
  return { candidate_id: session.candidate_id, account_binding_id: session.account_binding_id, session_id: session.session_id };
}

export function gatewayConfiguration() {
  const secret = process.env.BROWSER_PAIRING_GATEWAY_SECRET ?? "";
  const origin = process.env.BROWSER_PAIRING_WEBSITE_ORIGIN ?? "";
  const backend = process.env.BACKEND_URL ?? "";
  if (!/^[a-f0-9]{64}$/.test(secret)) throw new Error("Browser pairing configuration is unavailable");
  const parsed = new URL(origin);
  const target = new URL(backend.replace(/\/api\/?$/, ""));
  if (parsed.protocol !== "https:" || parsed.origin !== origin || parsed.username || parsed.password
      || target.protocol !== "https:" || target.username || target.password || target.pathname !== "/"
      || target.search || target.hash) throw new Error("Browser pairing configuration is unavailable");
  return { key: Buffer.from(secret, "hex"), origin, backend: target.origin };
}

function mac(key: Buffer, domain: string, input: string) {
  return createHmac("sha256", key).update(`${domain}\n${input}`, "utf8").digest("hex");
}

export function issuePairingCsrf(key: Buffer, context: RetainedBrowserSession, now = Date.now()) {
  const token = randomBytes(32).toString("hex"), expires = now + 600000;
  const input = `${token}.${expires}.${JSON.stringify(context)}`;
  return { token, cookie: `${token}.${expires}.${mac(key, "hirewiz.browser-csrf.v1", input)}` };
}

export function verifyPairingCsrf(key: Buffer, context: RetainedBrowserSession, cookie: string | undefined,
                                  header: string | null, now = Date.now()) {
  if (!cookie || !header || !/^[a-f0-9]{64}$/.test(header)) return false;
  const match = /^([a-f0-9]{64})\.([0-9]{13})\.([a-f0-9]{64})$/.exec(cookie);
  if (!match || match[1] !== header) return false;
  const expires = Number(match[2]);
  if (!Number.isSafeInteger(expires) || !(now < expires && expires <= now + 600000)) return false;
  const expected = mac(key, "hirewiz.browser-csrf.v1", `${header}.${expires}.${JSON.stringify(context)}`);
  return timingSafeEqual(Buffer.from(match[3], "hex"), Buffer.from(expected, "hex"));
}

export function candidateGatewayHeaders(key: Buffer, origin: string, context: RetainedBrowserSession,
                                        path: string, raw: Buffer, now = Date.now()) {
  const bytes = Buffer.from(JSON.stringify({ version: 1, issuer: "hirewiz_bff_v1",
    audience: "hirewiz:browser-pairing-candidate", request_id: randomUUID(), method: "POST",
    path, origin, body_sha256: createHash("sha256").update(raw).digest("hex"),
    issued_at_ms: now, expires_at_ms: now + 5000, context }), "utf8");
  return { "Content-Type": "application/json", "X-Hirewiz-Gateway-Assertion": bytes.toString("base64url"),
    "X-Hirewiz-Gateway-Signature": createHmac("sha256", key).update("hirewiz.browser-gateway.v1\n").update(bytes).digest("hex") };
}

export async function boundedPairingBytes(stream: ReadableStream<Uint8Array> | null, maximum: number) {
  if (!stream) throw new Error("Pairing response is unavailable");
  const reader = stream.getReader(), chunks: Buffer[] = [];
  let size = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("Pairing read deadline exceeded")), 3000); });
  const read = async () => {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) throw new Error("Pairing size bound exceeded");
      chunks.push(Buffer.from(value));
    }
    return Buffer.concat(chunks);
  };
  try { return await Promise.race([read(), timeout]); }
  finally {
    clearTimeout(timer);
    // An underlying stream may never acknowledge cancellation. Cleanup cannot
    // extend the read deadline or replace the failure that caused it.
    try { void reader.cancel().catch(() => undefined); } catch { /* best effort */ }
    try { reader.releaseLock(); } catch { /* preserve the original result */ }
  }
}

export function candidateReply(operation: string, value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid pairing reply");
  const data = value as Record<string, unknown>;
  const uuid = (key: string) => typeof data[key] === "string" && UUID_PATTERN.test(data[key] as string);
  const exact = (keys: string[]) => Object.keys(data).sort().join(",") === keys.sort().join(",");
  if (data.status === "UNKNOWN" && exact(["status", "operation_id", "retry_allowed"]) && uuid("operation_id") && data.retry_allowed === false) return data;
  if (operation === "confirm" && exact(["status", "pairing_id", "device_id"]) && data.status === "CANDIDATE_CONFIRMED" && uuid("pairing_id") && uuid("device_id")) return data;
  if (operation === "revoke" && exact(["status", "device_id", "event_sequence"]) && data.status === "REVOKED" && uuid("device_id")
      && typeof data.event_sequence === "number" && Number.isSafeInteger(data.event_sequence) && data.event_sequence > 0) return data;
  const fields = operation === "challenge" ? ["challenge_id", "nonce", "operation", "pairing_id", "device_id", "key_sha256", "request_sha256", "expires_at_ms"]
    : ["challenge_id", "nonce", "operation", "device_id", "expires_at_ms"];
  if (!exact(fields) || !uuid("challenge_id") || !uuid("nonce") || !uuid("device_id")
      || typeof data.expires_at_ms !== "number" || !Number.isSafeInteger(data.expires_at_ms) || data.expires_at_ms <= 0
      || data.operation !== (operation === "challenge" ? "confirm_pairing" : "revoke_device")) throw new Error("Invalid pairing reply");
  if (operation === "challenge" && (!uuid("pairing_id") || typeof data.key_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(data.key_sha256)
      || typeof data.request_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(data.request_sha256))) throw new Error("Invalid pairing reply");
  return data;
}

function strictPairingObject(value: unknown, fields: string[]) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid pairing reply");
  const data = value as Record<string, unknown>;
  if (Object.keys(data).sort().join(",") !== fields.toSorted().join(",")) throw new Error("Invalid pairing reply");
  const ids = new Set(["operation_id", "pairing_id", "device_id", "authority_id", "epoch_id", "challenge_id", "claim_id", "subject_uuid", "session_id", "nonce"]);
  const digests = new Set(["key_sha256", "nonce_sha256", "request_sha256", "principal_sha256", "confirmation_sha256", "event_sha256"]);
  const numbers = new Set(["authority_incarnation", "epoch_generation", "key_generation", "auth_generation", "event_sequence", "issued_at_ms", "expires_at_ms"]);
  for (const name of fields) {
    if (ids.has(name) && (typeof data[name] !== "string" || !UUID_PATTERN.test(data[name] as string))) throw new Error("Invalid pairing reply");
    if (digests.has(name) && (typeof data[name] !== "string" || !/^[a-f0-9]{64}$/.test(data[name] as string))) throw new Error("Invalid pairing reply");
    if (numbers.has(name) && (typeof data[name] !== "number" || !Number.isSafeInteger(data[name]) || (data[name] as number) <= 0)) throw new Error("Invalid pairing reply");
    if (["extension_id", "executor_revision"].includes(name) && (typeof data[name] !== "string" || !/^[A-Za-z0-9_-]{1,160}$/.test(data[name] as string))) throw new Error("Invalid pairing reply");
  }
  return data;
}
function identityPayload(value: unknown, fields: string[], operation: string, lifetime: number, issuer = false) {
  const data = strictPairingObject(value, fields);
  if (data.protocol_version !== 2 || data.operation !== operation || data.audience !== "hirewiz:pairing-only"
      || (issuer && data.issuer !== "hirewiz-pairing") || (data.expires_at_ms as number) <= (data.issued_at_ms as number)
      || (data.expires_at_ms as number) - (data.issued_at_ms as number) > lifetime) throw new Error("Invalid pairing reply");
  return data;
}

export function deviceReply(operation: string, value: unknown) {
  if (value && typeof value === "object" && (value as Record<string, unknown>).status === "UNKNOWN") return candidateReply(operation, value);
  if (operation === "status" || operation === "create") {
    const data = strictPairingObject(value, ["pairing_id", "device_id", "status"]);
    const allowed = operation === "create" ? ["REQUESTED"] : ["UNPROVEN", "REQUESTED", "CANDIDATE_CONFIRMED", "COMPLETED", "REVOKED", "EXPIRED"];
    if (!allowed.includes(data.status as string)) throw new Error("Invalid pairing reply");
    return data;
  }
  const realm = ["protocol_version", "operation", "audience", "device_id", "authority_id", "authority_incarnation", "epoch_id", "epoch_generation", "issued_at_ms", "expires_at_ms"];
  if (operation === "prepare") {
    const data = strictPairingObject(value, ["request", "nonce"]);
    const request = identityPayload(data.request, [...realm, "issuer", "operation_id", "pairing_id", "extension_id", "executor_revision", "public_key", "key_sha256", "challenge_id", "nonce_sha256"], "create_pairing", 120000, true);
    const key = strictPairingObject(request.public_key, ["kty", "crv", "x", "y"]);
    if (key.kty !== "EC" || key.crv !== "P-256" || typeof key.x !== "string" || typeof key.y !== "string"
        || !/^[A-Za-z0-9_-]{43}$/.test(key.x) || !/^[A-Za-z0-9_-]{43}$/.test(key.y)) throw new Error("Invalid pairing reply");
    return data;
  }
  if (operation === "challenge" || operation === "refresh-challenge") {
    const data = strictPairingObject(value, ["payload", "nonce"]);
    const fields = [...realm, "challenge_id", "subject_uuid", "key_sha256", "auth_generation", "nonce_sha256",
      ...(operation === "challenge" ? ["pairing_id", "request_sha256", "session_id", "principal_sha256", "confirmation_sha256"] : ["key_generation"])];
    identityPayload(data.payload, fields, operation === "challenge" ? "complete_pairing" : "refresh_claim", 10000);
    return data;
  }
  if (operation === "complete" || operation === "refresh") {
    const data = strictPairingObject(value, ["status", "device_claim"]);
    if (data.status !== (operation === "complete" ? "COMPLETED" : "IDENTIFIED")) throw new Error("Invalid pairing reply");
    const signed = strictPairingObject(data.device_claim, ["payload", "signature"]);
    if (typeof signed.signature !== "string" || !/^[A-Za-z0-9_-]{86}$/.test(signed.signature)) throw new Error("Invalid pairing reply");
    identityPayload(signed.payload, [...realm, "issuer", "claim_id", "subject_uuid", "key_sha256", "key_generation", "auth_generation", "extension_id", "executor_revision", "event_sequence", "event_sha256"], "device_identity", 300000, true);
    return data; // Original signed values are preserved; no field is normalized/replaced.
  }
  throw new Error("Invalid pairing reply");
}
