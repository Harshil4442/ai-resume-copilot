// Identity-only v2 transport. This does not enable the fixture action protocol.
import { base64url, canonical, decode64, digest } from "./protocol.js";

const encoder = new TextEncoder();
// Allow the bounded native operation to settle before the outer client gives up.
// This does not extend any signed request, challenge, claim or action deadline.
const REQUEST_TIMEOUT_MS = 40000;
const TIMED_READS = new Set(["status", "inspect-key"]);
const MAX_TIMING_MS = 86400000;
const timingInteger = (value, minimum = -MAX_TIMING_MS) => Number.isSafeInteger(value)
  && value >= minimum && value <= MAX_TIMING_MS ? value : null;
function monotonicTiming() {
  try { const value = performance.now(); return Number.isFinite(value) ? value : null; }
  catch { return null; }
}
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const CLAIM_KEYS = ["protocol_version", "operation", "issuer", "audience", "claim_id", "device_id", "subject_uuid", "key_sha256", "key_generation", "auth_generation", "authority_id", "authority_incarnation", "epoch_id", "epoch_generation", "extension_id", "executor_revision", "event_sequence", "event_sha256", "issued_at_ms", "expires_at_ms"].sort().join(",");
const domain = (kind, data) => encoder.encode(`hirewiz.pairing.${kind}.v2\n${canonical(data)}`);
const publicKey = (jwk) => ({ kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y });
function cancelBestEffort(stream) {
  try { void stream?.cancel().catch(() => undefined); } catch { /* cleanup must not replace the transport result */ }
}

export class PairingTransport {
  #readTiming = null;
  #readAttempt = null;

  constructor({ configuration, identity, now = () => Date.now(), beforePost = async () => undefined }) {
    let local = false;
    try {
      const origin = new URL(configuration.websiteOrigin);
      local = configuration.mode === "local_native_fixture" && configuration.testScope === "owned_local_native_connection"
        && origin.protocol === "https:" && origin.origin === configuration.websiteOrigin
        && ["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname) && !origin.username && !origin.password;
    } catch { /* invalid fixed configuration */ }
    if (!(configuration.mode === "pairing_only" && configuration.websiteOrigin === "https://www.hirewizhq.com" || local)
        || !/^[a-p]{32}$/.test(configuration.extensionId) || !configuration.executorRevision || !configuration.authorityKey) {
      throw new Error("Reviewed production pairing configuration is unavailable");
    }
    Object.assign(this, { configuration, identity, now, beforePost });
    this.signal = null;
    this.pairingId = null; this.deviceId = null; this.claim = null;
  }
  async sign(kind, payload) {
    return base64url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, this.identity.private_key, domain(kind, payload)));
  }
  async keyDigest() {
    return digest(domain("public-key", publicKey(this.identity.public_key)));
  }
  readTiming() {
    return this.#readTiming === null ? null : { ...this.#readTiming };
  }
  async post(operation, body) {
    const attempt = TIMED_READS.has(operation) ? {} : null;
    if (attempt) { this.#readAttempt = attempt; this.#readTiming = null; }
    await this.beforePost();
    if (this.signal?.aborted) throw new Error("Connection request was paused");
    let issued = null, age = null;
    if (attempt) {
      try {
        const timestamp = body?.issued_at_ms;
        if (Number.isSafeInteger(timestamp) && timestamp > 0) {
          issued = timestamp;
          age = timingInteger(this.now() - timestamp);
        }
      } catch { /* Diagnostics cannot replace the original transport outcome. */ }
    }
    const started = attempt ? monotonicTiming() : null;
    let status = null;
    try {
      const response = await fetch(`${this.configuration.websiteOrigin}/api/browser-pairing/device/${operation}`, {
        method: "POST", credentials: "omit", redirect: "error", cache: "no-store",
        signal: this.signal ? AbortSignal.any([AbortSignal.timeout(REQUEST_TIMEOUT_MS), this.signal]) : AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (attempt) {
        try {
          const observed = response.status;
          if (Number.isInteger(observed) && observed >= 100 && observed <= 599) status = observed;
        } catch { /* A diagnostic cannot replace the response outcome. */ }
      }
      if (!response.ok) { cancelBestEffort(response.body); throw new Error("Current pairing is unavailable; no action was authorized"); }
      const reader = response.body.getReader(), chunks = [];
      let size = 0;
      let timer;
      const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Pairing read deadline exceeded")), 3000); });
      const read = async () => {
        for (;;) {
          const next = await reader.read(); if (next.done) break;
          size += next.value.byteLength;
          if (size > 16384) throw new Error("Pairing response exceeded its bound");
          chunks.push(next.value);
        }
      };
      try { await Promise.race([read(), timeout]); }
      finally {
        clearTimeout(timer);
        // Cancellation acknowledgement is untrusted and is never awaited.
        cancelBestEffort(reader);
        try { reader.releaseLock(); } catch { /* preserve the original result */ }
      }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const data = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Invalid pairing response");
      if (data.status === "UNKNOWN") {
        const failure = new Error("Pairing outcome is unknown; retrying the operation is disabled");
        failure.operationId = UUID.test(data.operation_id) ? data.operation_id : null;
        throw failure;
      }
      return data;
    } catch (failure) {
      if (attempt && this.#readAttempt === attempt) {
        const ended = monotonicTiming();
        this.#readTiming = Object.freeze({ version: 1, issued_at_ms: issued, issued_age_ms: age,
          elapsed_ms: started === null || ended === null ? null : timingInteger(Math.round(ended - started), 0),
          http_status: status });
      }
      throw failure;
    }
  }
  async prepare({ onPrepared, beforeCreate } = {}) {
    if (typeof onPrepared !== "function" || typeof beforeCreate !== "function") throw new Error("Durable connection checkpoints are required before preparation");
    this.claim = null;
    const key = publicKey(this.identity.public_key);
    const payload = { protocol_version: 2, operation: "prepare_request", key, extension_id: this.configuration.extensionId,
      revision: this.configuration.executorRevision, request_id: crypto.randomUUID(), issued_at_ms: this.now() };
    const prepared = await this.post("prepare", { ...payload, signature: await this.sign("transport-request", payload) });
    const request = prepared.request;
    if (!request || !UUID.test(request.pairing_id) || !UUID.test(request.device_id) || !UUID.test(prepared.nonce)
        || canonical(request.public_key) !== canonical(key) || request.key_sha256 !== await digest(domain("public-key", key))
        || request.extension_id !== this.configuration.extensionId || request.executor_revision !== this.configuration.executorRevision
        || request.expires_at_ms <= this.now()) throw new Error("Pairing request does not match this exact device");
    const review = { pairing_id: request.pairing_id, device_id: request.device_id, key_sha256: request.key_sha256,
      request_sha256: await digest(domain("request", request)), expires_at_ms: request.expires_at_ms,
      review_url: `${this.configuration.websiteOrigin}/browser-companion?pairing=${request.pairing_id}` };
    // Only public reconciliation metadata crosses the durable checkpoint boundary.
    // A failed/lost checkpoint acknowledgement must stop before creation.
    await onPrepared(review);
    await beforeCreate();
    const created = await this.post("create", { pairing_id: request.pairing_id, nonce: prepared.nonce, signature: await this.sign("request", request) });
    if (created.status !== "REQUESTED" || created.pairing_id !== request.pairing_id || created.device_id !== request.device_id) throw new Error("Pairing creation did not return confirmed identity state");
    this.pairingId = request.pairing_id; this.deviceId = request.device_id;
    return review;
  }
  async inspectKey() {
    const key = publicKey(this.identity.public_key);
    const payload = { protocol_version: 2, operation: "key_status", key, extension_id: this.configuration.extensionId,
      revision: this.configuration.executorRevision, request_id: crypto.randomUUID(), issued_at_ms: this.now() };
    const result = await this.post("inspect-key", { ...payload, signature: await this.sign("transport-request", payload) });
    if (Object.keys(result).sort().join(",") !== "executor_revision,extension_id,key_sha256,protocol_version,status"
        || result.protocol_version !== 2 || result.key_sha256 !== await this.keyDigest()
        || result.extension_id !== this.configuration.extensionId || result.executor_revision !== this.configuration.executorRevision
        || !["UNCLAIMED", "CLAIMED"].includes(result.status)) throw new Error("Key inspection does not match this exact browser");
    return result;
  }
  async lookup(operation, identity) {
    const payload = { protocol_version: 2, operation, identity, request_id: crypto.randomUUID(), issued_at_ms: this.now() };
    const path = { device_challenge: "challenge", refresh_challenge: "refresh-challenge", status: "status" }[operation];
    return this.post(path, { ...payload, signature: await this.sign("transport-request", payload) });
  }
  async identify(refresh = false) {
    if (!this.pairingId || !this.deviceId) throw new Error("A candidate-reviewed pairing is required");
    const challenge = await this.lookup(refresh ? "refresh_challenge" : "device_challenge", refresh ? this.deviceId : this.pairingId);
    const payload = challenge.payload;
    if (!payload || payload.device_id !== this.deviceId || !UUID.test(payload.challenge_id) || !UUID.test(challenge.nonce)
        || payload.expires_at_ms <= this.now()) throw new Error("Device challenge does not match this pairing");
    const result = await this.post(refresh ? "refresh" : "complete", { challenge_id: payload.challenge_id, nonce: challenge.nonce,
      signature: await this.sign("device-challenge", payload) });
    const envelope = result.device_claim;
    const claim = envelope?.payload;
    if (!claim || Object.keys(claim).sort().join(",") !== CLAIM_KEYS || claim.protocol_version !== 2 || claim.operation !== "device_identity"
        || claim.audience !== "hirewiz:pairing-only" || claim.issuer !== "hirewiz-pairing" || claim.device_id !== this.deviceId
        || claim.key_sha256 !== await digest(domain("public-key", publicKey(this.identity.public_key)))
        || claim.extension_id !== this.configuration.extensionId || claim.executor_revision !== this.configuration.executorRevision
        || claim.issued_at_ms > this.now() || claim.expires_at_ms <= this.now() || claim.expires_at_ms - claim.issued_at_ms > 300000) {
      throw new Error("Invalid pairing-only identity claim");
    }
    const key = await crypto.subtle.importKey("jwk", this.configuration.authorityKey, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    if (!await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, decode64(envelope.signature), domain("device-claim", claim))) throw new Error("Untrusted pairing authority");
    this.claim = envelope;
    return { device_id: this.deviceId, paired: true, allowed_actions: [] };
  }
}
