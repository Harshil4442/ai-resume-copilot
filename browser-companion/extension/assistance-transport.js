import { base64url, canonical, decode64, digest } from "./protocol.js";

const encode = new TextEncoder();
const domain = (kind, value) => encode.encode(`hirewiz.browser-assistance.${kind}.v1\n${canonical(value)}`);
const publicKey = (jwk) => ({ kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y });
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const unavailable = "Browser assistance is unavailable. Check its status before another operation; no automatic retry occurred.";
const cancel = (stream) => { try { void stream?.cancel().catch(() => undefined); } catch { /* preserve outcome */ } };

export class AssistanceTransport {
  constructor({ configuration, identity, beforePost, now = () => Date.now(), fetcher = (...arguments_) => globalThis.fetch(...arguments_) }) {
    if (configuration.mode !== "reviewed_assistance" || configuration.websiteOrigin !== "https://www.hirewizhq.com"
      || configuration.portalOrigin !== "https://job-boards.greenhouse.io"
      || configuration.extensionId !== "pnjmegppacleipajnifjmbcopjjffklb" || !UUID.test(identity.device_id)
      || typeof beforePost !== "function") throw new Error(unavailable);
    Object.assign(this, { configuration, identity, beforePost, now, fetcher });
    this.authorityKey = null;
  }
  async post(operation, body) {
    if (!["prepare", "status", "application", "inspect", "begin", "artifact", "outcome", "cancel"].includes(operation)) throw new Error(unavailable);
    await this.beforePost();
    const unsigned = { protocol_version: 1, device_id: this.identity.device_id, request_id: crypto.randomUUID(),
      operation, issued_at_ms: this.now(), body };
    const signature = base64url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, this.identity.private_key, domain("request", unsigned)));
    const bytes = JSON.stringify({ ...unsigned, signature });
    if (encode.encode(bytes).byteLength > 65536) throw new Error("The inspected form exceeds the supported assistance size.");
    return this.fetcher(`${this.configuration.websiteOrigin}/api/browser-assistance/device/${operation}`, {
      method: "POST", credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(12000),
      headers: { "Content-Type": "application/json" }, body: bytes,
    });
  }
  async request(operation, body) {
    if (operation === "artifact") throw new Error("Binary delivery requires the exact attachment action");
    const response = await this.post(operation, body);
    if (!response.ok) { cancel(response.body); throw new Error(unavailable); }
    const reader = response.body?.getReader();
    if (!reader) throw new Error(unavailable);
    const chunks = []; let size = 0; let timer;
    const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(unavailable)), 3000); });
    const read = async () => {
      for (;;) {
        const next = await reader.read(); if (next.done) break;
        size += next.value.byteLength; if (size > 65536) throw new Error(unavailable);
        chunks.push(next.value);
      }
    };
    try { await Promise.race([read(), timeout]); }
    finally { clearTimeout(timer); cancel(reader); try { reader.releaseLock(); } catch { /* preserve outcome */ } }
    const all = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.byteLength; }
    const result = JSON.parse(new TextDecoder("utf8", { fatal: true }).decode(all));
    if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error(unavailable);
    return result;
  }
  async artifact(lease) {
    if (lease.review_protocol_version !== 3 || canonical(lease.allowed_actions) !== '["attach"]') throw new Error(unavailable);
    const response = await this.post("artifact", { review_id: lease.review_id, action_id: lease.action_id, review_digest: lease.review_digest });
    const artifact = lease.artifact;
    if (!response.ok || response.headers.get("content-type") !== artifact.media_type
      || response.headers.get("content-length") !== String(artifact.size_bytes)
      || response.headers.get("x-artifact-sha256") !== artifact.sha256
      || response.headers.get("x-artifact-filename") !== artifact.filename
      || response.headers.has("content-encoding") || response.headers.has("content-range")
      || !Number.isSafeInteger(artifact.size_bytes) || artifact.size_bytes < 1 || artifact.size_bytes > 10 * 1024 * 1024) {
      cancel(response.body); throw new Error("The exact sealed file delivery could not be verified. No automatic retry occurred.");
    }
    const reader = response.body?.getReader();
    if (!reader) throw new Error(unavailable);
    const chunks = []; let size = 0; let timer;
    const read = async () => {
      for (;;) {
        const next = await reader.read(); if (next.done) break;
        size += next.value.byteLength;
        if (size > artifact.size_bytes) throw new Error(unavailable);
        chunks.push(next.value);
      }
    };
    try { await Promise.race([read(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(unavailable)), 3000); })]); }
    finally { clearTimeout(timer); cancel(reader); try { reader.releaseLock(); } catch { /* preserve unknown */ } }
    if (size !== artifact.size_bytes) throw new Error(unavailable);
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    if (await digest(bytes) !== artifact.sha256 || this.now() >= lease.deadline) throw new Error("The exact file hash or attachment lease changed. No automatic retry occurred.");
    return bytes;
  }
  async device(operation = "status", reviewId = null) {
    const body = operation === "prepare" ? { public_key: publicKey(this.identity.public_key), extension_id: this.configuration.extensionId }
      : reviewId ? { review_id: reviewId } : {};
    const result = await this.request(operation, body);
    if (result.id !== this.identity.device_id || result.extension_id !== this.configuration.extensionId
      || result.key_sha256 !== await digest(canonical(publicKey(this.identity.public_key)))
      || !["pending", "active", "revoked"].includes(result.state)) throw new Error(unavailable);
    const key = result.authority_key;
    if (!key || Object.keys(key).sort().join(",") !== "crv,kty,x,y" || key.kty !== "EC" || key.crv !== "P-256"
      || !/^[A-Za-z0-9_-]{43}$/.test(key.x) || !/^[A-Za-z0-9_-]{43}$/.test(key.y)) throw new Error(unavailable);
    await crypto.subtle.importKey("jwk", key, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    this.authorityKey = key;
    return result;
  }
  async verifyLease(envelope) {
    if (!this.authorityKey || !envelope || Object.keys(envelope).sort().join(",") !== "payload,signature"
      || typeof envelope.signature !== "string" || envelope.signature.length !== 86) throw new Error("The exact action signature is unavailable.");
    const payload = envelope.payload;
    const key = await crypto.subtle.importKey("jwk", this.authorityKey, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    if (!await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, decode64(envelope.signature), domain("lease", payload))) throw new Error("The exact action signature could not be verified.");
    if (payload.protocol_version !== 1 || payload.device_id !== this.identity.device_id || !UUID.test(payload.action_id)
      || !Number.isSafeInteger(payload.issued_at_ms) || !Number.isSafeInteger(payload.deadline)
      || payload.issued_at_ms > this.now() + 2000 || payload.deadline <= this.now()
      || payload.deadline <= payload.issued_at_ms || payload.deadline - payload.issued_at_ms > 10000
      || canonical(payload.allowed_actions) !== (payload.review_protocol_version === 3 ? '["attach"]' : '["fill"]')) throw new Error("The reviewed field action expired or changed.");
    return payload;
  }
}
