import assert from "node:assert/strict";
import test from "node:test";
import { PairingRuntime } from "../extension/pairing-runtime.js";

const uuid = () => crypto.randomUUID();
async function fixture(stored = null) {
  let checkpoint = stored, permission = true;
  const calls = [];
  const configuration = { websiteOrigin: "https://www.hirewizhq.com", extensionId: "a".repeat(32), executorRevision: "owned_test", authorityKey: { public: "synthetic_test_only" } };
  const review = { pairing_id: uuid(), device_id: uuid(), key_sha256: "a".repeat(64), request_sha256: "b".repeat(64), expires_at_ms: Date.now() + 120000 };
  review.review_url = `${configuration.websiteOrigin}/browser-companion?pairing=${review.pairing_id}`;
  const transport = { configuration, claim: null,
    keyDigest: async () => "a".repeat(64),
    prepare: async () => { calls.push("prepare"); return review; },
    lookup: async () => { calls.push("status"); return { pairing_id: review.pairing_id, device_id: review.device_id, status: "CANDIDATE_CONFIRMED" }; },
    identify: async (refresh) => { calls.push(refresh ? "refresh" : "identify"); transport.claim = { payload: { expires_at_ms: Date.now() + 300000 } }; return { paired: true, allowed_actions: [] }; },
  };
  const store = { read: async () => structuredClone(checkpoint), write: async (value) => { checkpoint = structuredClone(value); } };
  const checkPermission = async () => { if (!permission) throw new Error("Permission revoked"); };
  const runtime = await new PairingRuntime({ transport, store, checkPermission }).initialise();
  return { runtime, transport, calls, review, store, checkpoint: () => structuredClone(checkpoint), revoke: () => { permission = false; } };
}

test("connection intent is durable before any transport operation and grants zero application actions", async () => {
  const f = await fixture();
  f.transport.prepare = async () => { assert.equal(f.checkpoint().phase, "preparing"); return f.review; };
  assert.equal((await f.runtime.start()).phase, "awaiting_candidate");
  assert.equal((await f.runtime.complete()).connected, true);
  assert.equal((await f.runtime.refresh()).busy, false);
  assert.deepEqual(f.runtime.status().allowed_actions, []);
  assert.deepEqual(Object.keys(f.checkpoint()).filter((key) => /password|cookie|csrf|assertion|claim/.test(key)), []);
});

test("lost prepare response is retained unknown across restart and cancel never permits a new request", async () => {
  const f = await fixture();
  f.transport.prepare = async () => { f.calls.push("prepare"); throw new Error("Owned lost response"); };
  await assert.rejects(f.runtime.start(), /needs inspection/);
  assert.equal(f.checkpoint().phase, "unknown");
  const fresh = await new PairingRuntime({ transport: f.transport, store: f.store, checkPermission: async () => undefined }).initialise();
  await fresh.cancel();
  await assert.rejects(fresh.start(), /blocked/);
  assert.deepEqual(f.calls, ["prepare"]);
});

test("worker interruption of a pending intent becomes unknown without a transport request", async () => {
  const f = await fixture();
  await f.runtime.save({ phase: "preparing", local_operation_id: uuid() });
  const fresh = await new PairingRuntime({ transport: f.transport, store: f.store, checkPermission: async () => undefined }).initialise();
  assert.equal(fresh.status().phase, "unknown");
  assert.deepEqual(f.calls, []);
});

test("completion before website confirmation is a status read and does not send a new native challenge", async () => {
  const f = await fixture(); await f.runtime.start();
  f.transport.lookup = async () => ({ pairing_id: f.review.pairing_id, device_id: f.review.device_id, status: "REQUESTED" });
  const result = await f.runtime.complete();
  assert.equal(result.phase, "awaiting_candidate");
  assert.match(result.message, /Review this browser/);
  assert.deepEqual(f.calls, ["prepare"]);
});

test("known pairing survives restart with no cached authority and refresh is explicit", async () => {
  const f = await fixture(); await f.runtime.start(); await f.runtime.complete();
  const restartedTransport = { ...f.transport, claim: null };
  const fresh = await new PairingRuntime({ transport: restartedTransport, store: f.store, checkPermission: async () => undefined }).initialise();
  assert.equal(fresh.status().phase, "paired"); assert.equal(fresh.status().connected, false);
  assert.deepEqual(f.calls, ["prepare", "status", "identify"]);
});

test("retained connection cannot resume with a different otherwise valid device key", async () => {
  const f = await fixture(); await f.runtime.start();
  const changed = { ...f.transport, keyDigest: async () => "c".repeat(64) };
  await assert.rejects(new PairingRuntime({ transport: changed, store: f.store, checkPermission: async () => undefined }).initialise(), /different retained device key/);
  assert.deepEqual(f.calls, ["prepare"]);
});

test("permission revocation prevents a transport request and a known pause can resume without network", async () => {
  const f = await fixture(); await f.runtime.start();
  await f.runtime.cancel(); assert.equal(f.checkpoint().phase, "paused");
  await f.runtime.resume(); assert.equal(f.checkpoint().phase, "awaiting_candidate");
  f.revoke(); await assert.rejects(f.runtime.complete(), /revoked/);
  assert.deepEqual(f.calls, ["prepare"]);
});

test("cancel during pending completion aborts transport and cannot be overwritten by a late success", async () => {
  const f = await fixture(); await f.runtime.start();
  let release, started;
  const entered = new Promise((resolve) => { started = resolve; });
  f.transport.identify = async () => { started(); await new Promise((resolve) => { release = resolve; }); return { paired: true }; };
  const completed = f.runtime.complete(); await entered;
  const signal = f.transport.signal;
  await f.runtime.cancel(); assert.equal(signal.aborted, true);
  release(); await assert.rejects(completed, /needs inspection/);
  assert.equal(f.checkpoint().phase, "unknown");
  await assert.rejects(f.runtime.refresh(), /known paired/);
});

test("duplicate completion is blocked before a second identity mutation", async () => {
  const f = await fixture(); await f.runtime.start();
  let release, started; const entered = new Promise((resolve) => { started = resolve; });
  f.transport.identify = async () => { f.calls.push("identify"); started(); await new Promise((resolve) => { release = resolve; }); return { paired: true }; };
  const completed = f.runtime.complete(); await entered;
  await assert.rejects(f.runtime.complete(), /reviewed pending/);
  release(); await completed;
  assert.equal(f.calls.filter((call) => call === "identify").length, 1);
});

test("cancel during checkpoint acknowledgement cannot be overwritten by a late paired save", async () => {
  const f = await fixture(); await f.runtime.start();
  const write = f.store.write;
  let release, started; const entered = new Promise((resolve) => { started = resolve; });
  f.store.write = async (value) => {
    if (value.phase === "paired") { started(); await new Promise((resolve) => { release = resolve; }); }
    return write(value);
  };
  const completion = f.runtime.complete(); await entered;
  const cancelled = f.runtime.cancel();
  assert.equal(f.runtime.status().phase, "unknown");
  await assert.rejects(f.runtime.refresh(), /known paired/);
  release(); await cancelled; await assert.rejects(completion, /needs inspection/);
  assert.equal(f.checkpoint().phase, "unknown");
});

test("corrupt checkpoints or changed constructor binding never silently create a new connection", async () => {
  const f = await fixture(); await f.runtime.start();
  const malformed = f.checkpoint(); malformed.password = "synthetic-untrusted-field";
  await assert.rejects(fixture(malformed), /Stored connection state/);
  const changed = { ...f.transport, configuration: { ...f.transport.configuration, executorRevision: "different_revision" } };
  await assert.rejects(new PairingRuntime({ transport: changed, store: f.store, checkPermission: async () => undefined }).initialise(), /Stored connection state/);
});
