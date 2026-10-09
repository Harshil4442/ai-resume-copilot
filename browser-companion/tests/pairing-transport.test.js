import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import test from "node:test";
import { PairingTransport } from "../extension/pairing-transport.js";
import { base64url, canonical } from "../extension/protocol.js";

const now = 1800000000000, extensionId = "a".repeat(32);
const bytes = (kind, data) => new TextEncoder().encode(`hirewiz.pairing.${kind}.v2\n${canonical(data)}`);
const sha = (kind, data) => createHash("sha256").update(bytes(kind, data)).digest("hex");
const uuid = () => crypto.randomUUID();

async function setup() {
  const device = await webcrypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  const authority = await webcrypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  const deviceJwk = await webcrypto.subtle.exportKey("jwk", device.publicKey);
  const key = { kty: deviceJwk.kty, crv: deviceJwk.crv, x: deviceJwk.x, y: deviceJwk.y };
  const configuration = { mode: "pairing_only", websiteOrigin: "https://www.hirewizhq.com", extensionId,
    executorRevision: "owned_synthetic_release", authorityKey: await webcrypto.subtle.exportKey("jwk", authority.publicKey) };
  const transport = new PairingTransport({ configuration, identity: { public_key: deviceJwk, private_key: device.privateKey }, now: () => now });
  const nonce = uuid();
  const request = { protocol_version: 2, operation: "create_pairing", issuer: "hirewiz-pairing", audience: "hirewiz:pairing-only",
    operation_id: uuid(), pairing_id: uuid(), device_id: uuid(), authority_id: uuid(), authority_incarnation: 1,
    epoch_id: uuid(), epoch_generation: 1, extension_id: extensionId, executor_revision: configuration.executorRevision,
    public_key: key, key_sha256: sha("public-key", key), challenge_id: uuid(), nonce_sha256: sha("nonce", { nonce }),
    issued_at_ms: now, expires_at_ms: now + 120000 };
  return { configuration, transport, device, authority, request, nonce };
}

test("disabled production configuration cannot start pairing or request a host permission", async () => {
  const fixture = await setup();
  assert.throws(() => new PairingTransport({ configuration: { ...fixture.configuration, mode: "disabled" }, identity: {} }), /unavailable/);
});

test("identity-only client uses raw domain-separated P-256 proofs without bearer or cookies", async (t) => {
  const f = await setup(), calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push(url);
    assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error");
    assert.equal(Object.keys(options.headers).join(","), "Content-Type");
    const payload = JSON.parse(options.body);
    const { signature, ...signed } = payload;
    const kind = url.endsWith("/prepare") ? "transport-request" : "request";
    const signedPayload = kind === "request" ? f.request : signed;
    assert.equal(await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, f.device.publicKey, Buffer.from(signature, "base64url"), bytes(kind, signedPayload)), true);
    if (url.endsWith("/prepare")) return Response.json({ request: f.request, nonce: f.nonce });
    return Response.json({ status: "REQUESTED", pairing_id: f.request.pairing_id, device_id: f.request.device_id });
  });
  const prepared = await f.transport.prepare();
  assert.equal(prepared.key_sha256, f.request.key_sha256);
  assert.equal(prepared.request_sha256, sha("request", f.request));
  assert.equal(calls.length, 2);
  assert.equal(f.transport.claim, null);
  await assert.rejects(webcrypto.subtle.exportKey("jwk", f.device.privateKey));
});

test("client refuses changed request key and does not proceed to creation", async (t) => {
  const f = await setup(); let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({ request: { ...f.request, key_sha256: "0".repeat(64) }, nonce: f.nonce }); });
  await assert.rejects(f.transport.prepare(), /exact device/);
  assert.equal(calls, 1);
});

test("an unknown commit never causes an automatic repeat POST", async (t) => {
  const f = await setup(); let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({ status: "UNKNOWN", operation_id: uuid(), retry_allowed: false }); });
  await assert.rejects(f.transport.prepare(), /retrying.*disabled/);
  assert.equal(calls, 1);
});

test("a genuine pairing-only authority signature grants no employer action", async (t) => {
  const f = await setup();
  f.transport.pairingId = f.request.pairing_id; f.transport.deviceId = f.request.device_id;
  const nonce = uuid();
  const challenge = { protocol_version: 2, audience: "hirewiz:pairing-only", operation: "complete_pairing", pairing_id: f.request.pairing_id,
    device_id: f.request.device_id, challenge_id: uuid(), nonce_sha256: sha("nonce", { nonce }), expires_at_ms: now + 10000 };
  const claim = { protocol_version: 2, operation: "device_identity", issuer: "hirewiz-pairing", audience: "hirewiz:pairing-only",
    claim_id: uuid(), device_id: f.request.device_id, subject_uuid: uuid(), key_sha256: f.request.key_sha256,
    key_generation: 1, auth_generation: 1, authority_id: f.request.authority_id, authority_incarnation: 1,
    epoch_id: f.request.epoch_id, epoch_generation: 1, extension_id: extensionId, executor_revision: f.configuration.executorRevision,
    event_sequence: 10, event_sha256: "c".repeat(64), issued_at_ms: now, expires_at_ms: now + 300000 };
  const signature = base64url(await webcrypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, f.authority.privateKey, bytes("device-claim", claim)));
  t.mock.method(globalThis, "fetch", async (url, options) => {
    const payload = JSON.parse(options.body);
    if (url.endsWith("/challenge")) return Response.json({ payload: challenge, nonce });
    assert.equal(await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, f.device.publicKey,
      Buffer.from(payload.signature, "base64url"), bytes("device-challenge", challenge)), true);
    return Response.json({ status: "COMPLETED", device_claim: { payload: claim, signature } });
  });
  assert.deepEqual(await f.transport.identify(), { device_id: f.request.device_id, paired: true, allowed_actions: [] });
});

for (const kind of ["timeout", "oversize", "http_error"]) {
  test(`actual companion ${kind} settles while underlying cancellation never acknowledges`, async (t) => {
    const f = await setup(); let cancelled = false, calls = 0;
    const body = new ReadableStream({
      start(controller) { if (kind === "oversize") controller.enqueue(new Uint8Array(16385)); },
      cancel() { cancelled = true; return new Promise(() => undefined); },
    });
    t.mock.method(globalThis, "fetch", async () => { calls++; return new Response(body, { status: kind === "http_error" ? 503 : 200 }); });
    let timer;
    const outcome = await Promise.race([
      f.transport.post("status", {}).then(() => "resolved", (error) => error.message),
      new Promise((resolve) => { timer = setTimeout(() => resolve("still pending"), kind === "timeout" ? 3500 : 250); }),
    ]);
    clearTimeout(timer);
    assert.equal(outcome, { timeout: "Pairing read deadline exceeded", oversize: "Pairing response exceeded its bound",
      http_error: "Current pairing is unavailable; no action was authorized" }[kind]);
    assert.equal(cancelled, true);
    assert.equal(body.locked, false);
    assert.equal(calls, 1);
    assert.equal(f.transport.claim, null);
  });
}

test("companion cancellation rejection preserves the original response bound failure", async (t) => {
  const f = await setup();
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(16385)); },
    cancel() { return Promise.reject(new Error("Owned cancellation refusal")); },
  });
  t.mock.method(globalThis, "fetch", async () => new Response(body));
  await assert.rejects(f.transport.post("status", {}), /Pairing response exceeded its bound/);
  assert.equal(body.locked, false);
  await new Promise((resolve) => setImmediate(resolve));
});
