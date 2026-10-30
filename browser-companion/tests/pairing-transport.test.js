import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import test from "node:test";
import { PairingTransport } from "../extension/pairing-transport.js";
import { PairingRuntime } from "../extension/pairing-runtime.js";
import { base64url, canonical } from "../extension/protocol.js";
import { fakeRequestDeadlines } from "./request-deadlines.js";

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

for (const delay of [100, 6000]) test(`read timing observes ${delay}ms beforePost delay without refreshing the signed request`, async (t) => {
  const f = await setup(); let clock = now, monotonic = 10, calls = 0;
  f.transport.now = () => clock;
  f.transport.beforePost = async () => { await Promise.resolve(); clock += delay; };
  t.mock.method(performance, "now", () => monotonic);
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls++;
    assert.equal(url, `${f.configuration.websiteOrigin}/api/browser-pairing/device/status`);
    assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store"); assert.deepEqual(options.headers, { "Content-Type": "application/json" });
    const { signature, ...payload } = JSON.parse(options.body);
    assert.equal(payload.issued_at_ms, now);
    assert.equal(await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, f.device.publicKey,
      Buffer.from(signature, "base64url"), bytes("transport-request", payload)), true);
    monotonic += 89;
    return Response.json({ detail: "synthetic-private-body" }, { status: 403 });
  });
  await assert.rejects(f.transport.lookup("status", uuid()), /Current pairing is unavailable/);
  assert.equal(calls, 1);
  assert.deepEqual(f.transport.readTiming(), { version: 1, issued_age_ms: delay, elapsed_ms: 89, http_status: 403 });
  const copy = f.transport.readTiming(); copy.http_status = 200;
  assert.equal(f.transport.readTiming().http_status, 403);
  assert.equal(JSON.stringify(f.transport.readTiming()).includes("synthetic-private"), false);
});

test("read timing preserves a negative age and uses monotonic elapsed after wall-clock rollback", async (t) => {
  const f = await setup(); let clock = now, monotonic = 100;
  f.transport.now = () => clock;
  f.transport.beforePost = async () => { clock -= 7; };
  t.mock.method(performance, "now", () => monotonic);
  const failure = Object.assign(new Error("synthetic-private-key-body"), { transport_timing: { raw: "must-not-copy" } });
  t.mock.method(globalThis, "fetch", async () => { clock -= 900; monotonic += 12; throw failure; });
  await assert.rejects(f.transport.lookup("status", uuid()), (error) => error === failure);
  assert.deepEqual(f.transport.readTiming(), { version: 1, issued_age_ms: -7, elapsed_ms: 12, http_status: null });
});

test("successful reads and a local permission failure clear the earlier RAM diagnostic without another send", async (t) => {
  const f = await setup(); let calls = 0, succeeds = false;
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    return Response.json(succeeds ? { status: "EXPIRED" } : {}, { status: succeeds ? 200 : 403 });
  });
  await assert.rejects(f.transport.lookup("status", uuid()));
  assert.equal(f.transport.readTiming().http_status, 403);
  succeeds = true; assert.deepEqual(await f.transport.lookup("status", uuid()), { status: "EXPIRED" });
  assert.equal(f.transport.readTiming(), null);
  succeeds = false; await assert.rejects(f.transport.lookup("status", uuid()));
  const permission = new Error("permission refused");
  f.transport.beforePost = async () => { throw permission; };
  await assert.rejects(f.transport.lookup("status", uuid()), (error) => error === permission);
  assert.equal(calls, 3); assert.equal(f.transport.readTiming(), null);
});

test("only status and key-inspection reads receive timing; mutation/challenge sends keep their original failure", async (t) => {
  const f = await setup(); let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({}, { status: 403 }); });
  await assert.rejects(f.transport.inspectKey());
  assert.equal(f.transport.readTiming().http_status, 403);
  for (const operation of ["prepare", "create", "challenge", "complete", "refresh-challenge", "refresh"]) {
    const next = await setup();
    await assert.rejects(next.transport.post(operation, { issued_at_ms: now, signature: "synthetic-private" }), /Current pairing is unavailable/);
    assert.equal(next.transport.readTiming(), null);
  }
  assert.equal(calls, 7);
});

test("an older failed read cannot overwrite a later successful attempt", async (t) => {
  const f = await setup(); let rejectFirst, entered;
  const firstEntered = new Promise((resolve) => { entered = resolve; });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    if (++calls === 1) { entered(); return new Promise((_, reject) => { rejectFirst = reject; }); }
    return Response.json({ status: "EXPIRED" });
  });
  const first = f.transport.lookup("status", uuid()); first.catch(() => undefined);
  await firstEntered;
  await f.transport.lookup("status", uuid());
  rejectFirst(new Error("old request failed")); await assert.rejects(first);
  assert.equal(calls, 2); assert.equal(f.transport.readTiming(), null);
});

test("invalid diagnostic clocks are unavailable and cannot replace the original HTTP error", async (t) => {
  const f = await setup();
  t.mock.method(performance, "now", () => NaN);
  t.mock.method(globalThis, "fetch", async () => Response.json({}, { status: 403 }));
  f.transport.now = () => Infinity;
  await assert.rejects(f.transport.post("status", { issued_at_ms: now }), /Current pairing is unavailable/);
  assert.deepEqual(f.transport.readTiming(), { version: 1, issued_age_ms: null, elapsed_ms: null, http_status: 403 });
});

test("throwing diagnostic clocks preserve the original failure and malformed successful bodies retain only status", async (t) => {
  const f = await setup();
  f.transport.now = () => { throw new Error("diagnostic-only clock failure"); };
  t.mock.method(performance, "now", () => { throw new Error("diagnostic-only monotonic failure"); });
  t.mock.method(globalThis, "fetch", async () => Response.json(["synthetic-private-response"]));
  await assert.rejects(f.transport.post("status", { issued_at_ms: now }), /Invalid pairing response/);
  assert.deepEqual(f.transport.readTiming(), { version: 1, issued_age_ms: null, elapsed_ms: null, http_status: 200 });
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
  const prepared = await f.transport.prepare({ onPrepared: async () => undefined, beforeCreate: async () => undefined });
  assert.equal(prepared.key_sha256, f.request.key_sha256);
  assert.equal(prepared.request_sha256, sha("request", f.request));
  assert.equal(calls.length, 2);
  assert.equal(f.transport.claim, null);
  await assert.rejects(webcrypto.subtle.exportKey("jwk", f.device.privateKey));
});

test("client refuses changed request key and does not proceed to creation", async (t) => {
  const f = await setup(); let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({ request: { ...f.request, key_sha256: "0".repeat(64) }, nonce: f.nonce }); });
  await assert.rejects(f.transport.prepare({ onPrepared: async () => undefined, beforeCreate: async () => undefined }), /exact device/);
  assert.equal(calls, 1);
});

test("an unknown commit never causes an automatic repeat POST", async (t) => {
  const f = await setup(); let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({ status: "UNKNOWN", operation_id: uuid(), retry_allowed: false }); });
  await assert.rejects(f.transport.prepare({ onPrepared: async () => undefined, beforeCreate: async () => undefined }), /retrying.*disabled/);
  assert.equal(calls, 1);
});

test("an abort-aware 13.025-second native response settles without the old twelve-second cutoff", async (t) => {
  const f = await setup(), deadlines = fakeRequestDeadlines(t);
  let calls = 0, started, signal, outcome = "pending";
  const dispatched = new Promise((resolve) => { started = resolve; });
  t.mock.method(globalThis, "fetch", (_url, options) => new Promise((resolve, reject) => {
    calls++; signal = options.signal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    setTimeout(() => resolve(Response.json({ status: "EXPIRED" })), 13025);
    started();
  }));
  const result = f.transport.post("status", {}).then((value) => { outcome = "resolved"; return value; },
    (error) => { outcome = "rejected"; throw error; });
  // Observe a possible old-deadline rejection without an unhandled promise.
  result.catch(() => undefined);
  await dispatched;
  t.mock.timers.tick(12000);
  await Promise.resolve();
  assert.equal(outcome, "pending");
  assert.equal(signal.aborted, false);
  t.mock.timers.tick(1025);
  assert.deepEqual(await result, { status: "EXPIRED" });
  assert.deepEqual(deadlines, [40000]);
  assert.equal(calls, 1);
  assert.equal(f.transport.claim, null);
});

test("a forty-second request timeout retains uncertainty and never repeats the POST", async (t) => {
  const f = await setup(), deadlines = fakeRequestDeadlines(t);
  let checkpoint = null, calls = 0, started, signal;
  const dispatched = new Promise((resolve) => { started = resolve; });
  const runtime = await new PairingRuntime({ transport: f.transport,
    store: { read: async () => structuredClone(checkpoint), write: async (value) => { checkpoint = structuredClone(value); } },
    checkPermission: async () => undefined, now: () => now }).initialise();
  t.mock.method(globalThis, "fetch", (_url, options) => new Promise((_resolve, reject) => {
    calls++; signal = options.signal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    started();
  }));
  const failed = assert.rejects(runtime.start(), /outcome needs inspection/);
  await dispatched;
  t.mock.timers.tick(39999);
  assert.equal(signal.aborted, false);
  t.mock.timers.tick(1);
  await failed;
  assert.equal(signal.aborted, true);
  assert.equal(checkpoint.phase, "unknown");
  assert.equal(checkpoint.send_kind, "prepare");
  assert.equal(checkpoint.pairing_id, null);
  await assert.rejects(runtime.start(), /starting another request is blocked/);
  t.mock.timers.tick(100000);
  assert.deepEqual(deadlines, [40000]);
  assert.equal(calls, 1);
  assert.equal(f.transport.claim, null);
});

test("explicit pause still aborts a request before its longer transport deadline", async (t) => {
  const f = await setup(), deadlines = fakeRequestDeadlines(t), pause = new AbortController();
  f.transport.signal = pause.signal;
  let calls = 0, started, signal;
  const dispatched = new Promise((resolve) => { started = resolve; });
  t.mock.method(globalThis, "fetch", (_url, options) => new Promise((_resolve, reject) => {
    calls++; signal = options.signal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    started();
  }));
  const failed = assert.rejects(f.transport.post("status", {}), /Explicit pause/);
  await dispatched;
  t.mock.timers.tick(1000);
  pause.abort(new Error("Explicit pause"));
  await failed;
  assert.equal(signal.aborted, true);
  assert.deepEqual(deadlines, [40000]);
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

for (const boundary of ["prepared_receipt", "create_intent"]) {
  test(`unacknowledged ${boundary} stops real transport before create`, async (t) => {
    const f = await setup(), calls = [];
    t.mock.method(globalThis, "fetch", async (url) => { calls.push(url); return Response.json({ request: f.request, nonce: f.nonce }); });
    await assert.rejects(f.transport.prepare({ onPrepared: async (review) => {
      assert.deepEqual(Object.keys(review).sort(), ["device_id", "expires_at_ms", "key_sha256", "pairing_id", "request_sha256", "review_url"]);
      assert.equal(review.request_sha256, sha("request", f.request));
      if (boundary === "prepared_receipt") throw new Error("Owned storage ACK loss");
    }, beforeCreate: async () => { if (boundary === "create_intent") throw new Error("Owned storage ACK loss"); } }), /storage ACK loss/);
    assert.equal(calls.length, 1); assert.match(calls[0], /\/prepare$/);
  });
}
for (const fault of ["create_ack", "create_checkpoint_ack"]) {
  test(`real signed ${fault} loss preserves public IDs without replay`, async (t) => {
    const f = await setup(), calls = []; let checkpoint = null, refused = false;
    const store = { read: async () => structuredClone(checkpoint), write: async (value) => {
      checkpoint = structuredClone(value);
      if (fault === "create_checkpoint_ack" && !refused && value.phase === "preparing" && value.send_kind === "create") {
        refused = true; throw new Error("Owned lost checkpoint ACK");
      }
    } };
    const runtime = await new PairingRuntime({ transport: f.transport, store, checkPermission: async () => undefined, now: () => now }).initialise();
    t.mock.method(globalThis, "fetch", async (url) => {
      calls.push(url.split("/").at(-1));
      if (url.endsWith("/prepare")) return Response.json({ request: f.request, nonce: f.nonce });
      if (url.endsWith("/create")) {
        assert.equal(checkpoint.send_kind, "create"); assert.equal(checkpoint.pairing_id, f.request.pairing_id);
        assert.equal(checkpoint.request_sha256, sha("request", f.request)); throw new Error("Owned lost create ACK");
      }
      if (url.endsWith("/status")) return Response.json({ pairing_id: f.request.pairing_id, device_id: f.request.device_id, status: "REQUESTED" });
      if (url.endsWith("/inspect-key")) return Response.json({ protocol_version: 2, key_sha256: f.request.key_sha256,
        extension_id: extensionId, executor_revision: f.configuration.executorRevision, status: "UNCLAIMED" });
      throw new Error("Unexpected operation");
    });
    await assert.rejects(runtime.start(), /needs inspection/);
    assert.equal(checkpoint.pairing_id, f.request.pairing_id); assert.equal(checkpoint.device_id, f.request.device_id);
    assert.equal(checkpoint.send_kind, fault === "create_ack" ? "create" : "prepare");
    assert.equal(JSON.stringify(checkpoint).includes(f.nonce), false);
    assert.equal(Object.keys(checkpoint).some((key) => /nonce|private|signature|claim/.test(key)), false);
    const restarted = await new PairingRuntime({ transport: f.transport, store, checkPermission: async () => undefined, now: () => now }).initialise();
    await assert.rejects(restarted.start(), /blocked/);
    const inspected = await restarted.inspectUnknown();
    if (fault === "create_ack") { assert.equal(inspected.can_resume_existing, true); await restarted.resumeExisting(); }
    else { assert.equal(inspected.can_archive_restart, true); await restarted.archiveUnknown(); }
    assert.deepEqual(calls, fault === "create_ack" ? ["prepare", "create", "status"] : ["prepare", "inspect-key"]);
  });
}
for (const change of [null, "CLAIMED", "key", "revision", "extra", "status"]) {
  test(`signed key inspection exact binding: ${change ?? "UNCLAIMED"}`, async (t) => {
    const f = await setup(); let count = 0;
    t.mock.method(globalThis, "fetch", async (url, options) => {
      count++; assert.match(url, /\/inspect-key$/);
      const { signature, ...payload } = JSON.parse(options.body);
      assert.equal(payload.operation, "key_status"); assert.equal(payload.revision, f.configuration.executorRevision);
      assert.equal(await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, f.device.publicKey,
        Buffer.from(signature, "base64url"), bytes("transport-request", payload)), true);
      const result = { protocol_version: 2, key_sha256: f.request.key_sha256, extension_id: extensionId,
        executor_revision: f.configuration.executorRevision, status: change === "CLAIMED" ? "CLAIMED" : "UNCLAIMED" };
      if (change === "key") result.key_sha256 = "f".repeat(64);
      if (change === "revision") result.executor_revision = "different";
      if (change === "extra") result.owner = "unexpected";
      if (change === "status") result.status = "UNKNOWN";
      return Response.json(result);
    });
    if (change === null || change === "CLAIMED") assert.equal((await f.transport.inspectKey()).status, change ?? "UNCLAIMED");
    else await assert.rejects(f.transport.inspectKey());
    assert.equal(count, 1); assert.equal(f.transport.claim, null);
  });
}

test("missing durable callbacks fail before any native prepare request", async (t) => {
  const f = await setup(); let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("Unexpected network"); });
  await assert.rejects(f.transport.prepare(), /Durable connection checkpoints/); assert.equal(calls, 0);
});

for (const [keyStatus, historyCount] of [["UNCLAIMED", 0], ["CLAIMED", 0], ["UNCLAIMED", 4], ["UNCLAIMED", 63], ["UNCLAIMED", 64]]) {
  test(`real signed transport/runtime: expired creation with ${keyStatus} key and ${historyCount} retained records is reconciled without replay`, async (t) => {
    const f = await setup(), calls = [];
    let clock = now, checkpoint = null, request = f.request, nonce = f.nonce;
    f.transport.now = () => clock;
    t.mock.method(globalThis, "fetch", async (url, options) => {
      const operation = new URL(url).pathname.split("/").at(-1);
      calls.push(operation);
      const { signature, ...payload } = JSON.parse(options.body);
      assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error");
      assert.equal(await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, f.device.publicKey,
        Buffer.from(signature, "base64url"), bytes(operation === "create" ? "request" : "transport-request",
          operation === "create" ? request : payload)), true);
      if (operation === "prepare") return Response.json({ request, nonce });
      if (operation === "create") return Response.json({ pairing_id: request.pairing_id, device_id: request.device_id, status: "REQUESTED" });
      if (operation === "status") {
        assert.equal(payload.identity, request.pairing_id);
        return Response.json({ pairing_id: request.pairing_id, device_id: request.device_id,
          status: clock >= request.expires_at_ms ? "EXPIRED" : "REQUESTED" });
      }
      assert.equal(operation, "inspect-key"); assert.deepEqual(payload.key, request.public_key);
      return Response.json({ protocol_version: 2, key_sha256: request.key_sha256, extension_id: extensionId,
        executor_revision: f.configuration.executorRevision, status: keyStatus });
    });
    const store = { read: async () => structuredClone(checkpoint), write: async (value) => { checkpoint = structuredClone(value); } };
    const runtime = await new PairingRuntime({ transport: f.transport, store, checkPermission: async () => undefined, now: () => clock }).initialise();
    await runtime.start(); clock += 120001;
    if (historyCount) {
      const { history, ...record } = checkpoint; assert.deepEqual(history, []);
      await runtime.save({ history: Array.from({ length: historyCount }, (_, index) => {
        const pairing_id = uuid();
        return { ...structuredClone(record), pairing_id, device_id: uuid(), local_operation_id: uuid(),
          review_url: `${f.configuration.websiteOrigin}/browser-companion?pairing=${pairing_id}`, updated_at_ms: clock - index - 1 };
      }) });
    }
    const original = structuredClone(checkpoint);
    const originalHistoryBytes = JSON.stringify(original.history);
    if (keyStatus === "CLAIMED") {
      await assert.rejects(runtime.inspectUnknown(), /claimed/);
      await assert.rejects(runtime.archiveUnknown(), /Inspect/); await assert.rejects(runtime.start(), /blocked/);
      assert.deepEqual(checkpoint, original);
      assert.deepEqual(calls, ["prepare", "create", "status", "inspect-key"]);
    } else if (historyCount === 64) {
      await runtime.inspectUnknown();
      await assert.rejects(runtime.archiveUnknown(), /history is full/);
      assert.deepEqual(checkpoint, original); assert.equal(JSON.stringify(checkpoint.history), originalHistoryBytes);
      assert.deepEqual(calls, ["prepare", "create", "status", "inspect-key"]);
      await assert.rejects(runtime.start(), /blocked/);
      const restarted = await new PairingRuntime({ transport: f.transport, store, checkPermission: async () => undefined, now: () => clock }).initialise();
      assert.deepEqual(checkpoint, original); assert.equal(restarted.status().can_archive_restart, false);
    } else {
      await runtime.inspectUnknown(); assert.deepEqual(checkpoint, original);
      await runtime.archiveUnknown(); assert.equal(checkpoint.phase, "idle");
      assert.deepEqual(calls, ["prepare", "create", "status", "inspect-key", "status", "inspect-key"]);
      const { reconciliation, ...old } = checkpoint.history.at(-1), { history, ...record } = original;
      assert.equal(checkpoint.history.length, historyCount + 1);
      assert.ok(new TextEncoder().encode(JSON.stringify(checkpoint)).byteLength <= 256 * 1024);
      if (historyCount === 63) assert.ok(JSON.stringify(checkpoint).length > 12288);
      assert.deepEqual(checkpoint.history.slice(0, -1), history); assert.deepEqual(old, record);
      assert.equal(JSON.stringify(checkpoint.history.slice(0, -1)), originalHistoryBytes);
      assert.equal(reconciliation.request_sha256, sha("request", request));
      assert.equal(reconciliation.inspected_at_ms, clock); assert.equal(reconciliation.rechecked_at_ms, clock);
      const restarted = await new PairingRuntime({ transport: f.transport, store, checkPermission: async () => undefined, now: () => clock }).initialise();
      assert.equal(restarted.status().phase, "idle"); assert.deepEqual(checkpoint.history.slice(0, -1), history);
      assert.deepEqual(calls, ["prepare", "create", "status", "inspect-key", "status", "inspect-key"]);
      const oldPairing = request.pairing_id;
      nonce = uuid(); request = { ...request, operation_id: uuid(), pairing_id: uuid(), device_id: uuid(),
        challenge_id: uuid(), nonce_sha256: sha("nonce", { nonce }), issued_at_ms: clock, expires_at_ms: clock + 120000 };
      await restarted.start(); assert.notEqual(checkpoint.pairing_id, oldPairing);
      assert.equal(checkpoint.key_sha256, original.key_sha256); assert.equal(checkpoint.history.at(-1).pairing_id, oldPairing);
      assert.deepEqual(calls, ["prepare", "create", "status", "inspect-key", "status", "inspect-key", "prepare", "create"]);
    }
    assert.equal(f.transport.claim, null);
    await assert.rejects(webcrypto.subtle.exportKey("jwk", f.device.privateKey));
  });
}
