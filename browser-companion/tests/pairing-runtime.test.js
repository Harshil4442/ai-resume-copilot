import assert from "node:assert/strict";
import test from "node:test";
import { PairingRuntime } from "../extension/pairing-runtime.js";
import { canonical, digest } from "../extension/protocol.js";

const uuid = () => crypto.randomUUID();
async function fixture(stored = null, configChanges = {}) {
  let checkpoint = stored, permission = true;
  const calls = [];
  const configuration = { websiteOrigin: "https://www.hirewizhq.com", extensionId: "a".repeat(32), executorRevision: "owned_test", authorityKey: { public: "synthetic_test_only" }, ...configChanges };
  const review = { pairing_id: uuid(), device_id: uuid(), key_sha256: "a".repeat(64), request_sha256: "b".repeat(64), expires_at_ms: Date.now() + 120000 };
  review.review_url = `${configuration.websiteOrigin}/browser-companion?pairing=${review.pairing_id}`;
  const transport = { configuration, claim: null,
    keyDigest: async () => "a".repeat(64),
    prepare: async ({ onPrepared, beforeCreate }) => { calls.push("prepare"); await onPrepared(review); await beforeCreate(); return review; },
    inspectKey: async () => { calls.push("inspect-key"); return { status: "UNCLAIMED" }; },
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
  f.transport.prepare = async ({ onPrepared, beforeCreate }) => {
    assert.equal(f.checkpoint().phase, "preparing"); assert.equal(f.checkpoint().send_kind, "prepare");
    await onPrepared(f.review);
    assert.equal(f.checkpoint().pairing_id, f.review.pairing_id); assert.equal(f.checkpoint().send_kind, "prepare");
    await beforeCreate(); assert.equal(f.checkpoint().send_kind, "create"); return f.review;
  };
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
  await f.runtime.save({ phase: "preparing", send_kind: "prepare", local_operation_id: uuid() });
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

for (const status of ["REQUESTED", "CANDIDATE_CONFIRMED"]) {
  test(`ambiguous create keeps IDs; ${status} enables only explicit same-connection resume`, async () => {
    const f = await fixture();
    f.transport.prepare = async ({ onPrepared, beforeCreate }) => {
      f.calls.push("prepare"); await onPrepared(f.review); await beforeCreate(); f.calls.push("create"); throw new Error("Lost create ACK");
    };
    await assert.rejects(f.runtime.start(), /needs inspection/);
    assert.equal(f.checkpoint().send_kind, "create"); assert.equal(f.checkpoint().pairing_id, f.review.pairing_id);
    const saved = f.checkpoint();
    const restarted = await new PairingRuntime({ transport: f.transport, store: f.store, checkPermission: async () => undefined }).initialise();
    f.transport.lookup = async () => { f.calls.push("status"); return { pairing_id: saved.pairing_id, device_id: saved.device_id, status }; };
    const inspected = await restarted.inspectUnknown();
    assert.equal(inspected.phase, "unknown"); assert.equal(inspected.can_resume_existing, true); assert.equal(inspected.can_archive_restart, false);
    await assert.rejects(restarted.start(), /blocked/); await assert.rejects(restarted.archiveUnknown(), /Inspect/);
    const resumed = await restarted.resumeExisting();
    assert.equal(resumed.phase, "awaiting_candidate"); assert.equal(resumed.pairing_id, saved.pairing_id);
    assert.equal(f.transport.pairingId, saved.pairing_id); assert.equal(f.transport.deviceId, saved.device_id);
    assert.deepEqual(f.calls, ["prepare", "create", "status"]);
  });
}
for (const operation of ["complete", "refresh"]) {
  test(`unknown ${operation} remains blocked despite confirmed status`, async () => {
    const f = await fixture(); await f.runtime.start(); if (operation === "refresh") await f.runtime.complete();
    f.transport.identify = async () => { throw new Error("Lost ACK"); };
    await assert.rejects(operation === "complete" ? f.runtime.complete() : f.runtime.refresh(), /needs inspection/);
    assert.equal(f.checkpoint().send_kind, operation);
    const inspected = await f.runtime.inspectUnknown();
    assert.equal(inspected.can_resume_existing, false); assert.equal(inspected.can_archive_restart, false);
    await assert.rejects(f.runtime.resumeExisting(), /Inspect/); await assert.rejects(f.runtime.archiveUnknown(), /Inspect/);
  });
}
test("prepared IDs without create intent require UNCLAIMED proof and separate archive/start clicks", async () => {
  const f = await fixture();
  await f.runtime.save({ phase: "preparing", send_kind: "prepare", local_operation_id: uuid(), ...f.review });
  const restarted = await new PairingRuntime({ transport: f.transport, store: f.store, checkPermission: async () => undefined }).initialise();
  assert.equal(restarted.status().phase, "unknown"); await assert.rejects(restarted.archiveUnknown(), /Inspect/);
  assert.equal((await restarted.inspectUnknown()).can_archive_restart, true); assert.equal(restarted.status().phase, "unknown");
  const archived = await restarted.archiveUnknown();
  assert.equal(archived.phase, "idle"); assert.equal(f.checkpoint().history[0].pairing_id, f.review.pairing_id);
  assert.equal(f.checkpoint().history[0].send_kind, "prepare"); assert.deepEqual(f.calls, ["inspect-key"]);
  await restarted.start(); assert.deepEqual(f.calls, ["inspect-key", "prepare"]);
});
for (const outcome of ["CLAIMED", "unavailable"]) {
  test(`ID-less prepare ${outcome} never enables archive/start`, async () => {
    const f = await fixture(); f.transport.prepare = async () => { throw new Error("Prepare unavailable"); };
    await assert.rejects(f.runtime.start(), /needs inspection/);
    f.transport.inspectKey = async () => { if (outcome === "unavailable") throw new Error("Inspection unavailable"); return { status: outcome }; };
    if (outcome === "unavailable") await assert.rejects(f.runtime.inspectUnknown(), /unavailable/);
    else assert.equal((await f.runtime.inspectUnknown()).can_archive_restart, false);
    await assert.rejects(f.runtime.archiveUnknown(), /Inspect/); await assert.rejects(f.runtime.start(), /blocked/);
    assert.equal(f.checkpoint().phase, "unknown");
  });
}
async function legacyCheckpoint() {
  const f = await fixture(null, { executorRevision: "companion_0_8_1" });
  const { history, send_kind, ...legacy } = f.runtime.empty(); assert.deepEqual(history, []); assert.equal(send_kind, null);
  return { ...legacy, version: 1, phase: "unknown", local_operation_id: uuid(), binding: await digest(canonical({
    website_origin: f.transport.configuration.websiteOrigin, extension_id: f.transport.configuration.extensionId,
    executor_revision: "companion_0_8_0", authority_key: f.transport.configuration.authorityKey })) };
}
test("exact legacy migration retains old checkpoint/key; restart needs new inspection", async () => {
  const legacy = await legacyCheckpoint(), f = await fixture(legacy, { executorRevision: "companion_0_8_1" });
  assert.equal(f.checkpoint().version, 2); assert.equal(f.checkpoint().phase, "unknown_legacy");
  assert.deepEqual(f.checkpoint().history, [legacy]); assert.equal(f.checkpoint().key_sha256, await f.transport.keyDigest()); assert.deepEqual(f.calls, []);
  assert.equal((await f.runtime.inspectUnknown()).can_archive_restart, true);
  const restarted = await new PairingRuntime({ transport: f.transport, store: f.store, checkPermission: async () => undefined }).initialise();
  await assert.rejects(restarted.archiveUnknown(), /Inspect/); await restarted.inspectUnknown(); await restarted.archiveUnknown();
  assert.equal(f.checkpoint().history.length, 2); assert.deepEqual(f.checkpoint().history[0], legacy);
  assert.equal(f.checkpoint().history[1].phase, "unknown_legacy"); assert.equal(f.checkpoint().phase, "idle");
  assert.deepEqual(f.calls, ["inspect-key", "inspect-key"]);
});
for (const change of ["origin", "authority", "extension", "revision", "known_id", "extra_field"]) {
  test(`legacy migration refuses ${change} drift`, async () => {
    const legacy = await legacyCheckpoint(), config = { executorRevision: "companion_0_8_1" };
    if (change === "origin") config.websiteOrigin = "https://other.example.test";
    if (change === "authority") config.authorityKey = { public: "changed" };
    if (change === "extension") config.extensionId = "b".repeat(32);
    if (change === "revision") config.executorRevision = "companion_0_8_2";
    if (change === "known_id") legacy.pairing_id = uuid();
    if (change === "extra_field") legacy.nonce = uuid();
    await assert.rejects(fixture(legacy, config), /Stored connection state/);
  });
}
test("migrated checkpoint rejects changed key and never truncates full history", async () => {
  const f = await fixture(await legacyCheckpoint(), { executorRevision: "companion_0_8_1" });
  const changed = { ...f.transport, keyDigest: async () => "c".repeat(64) };
  await assert.rejects(new PairingRuntime({ transport: changed, store: f.store, checkPermission: async () => undefined }).initialise(), /different retained device key/);
  const old = f.checkpoint().history[0]; await f.runtime.save({ history: [old, old, old, old] });
  await f.runtime.inspectUnknown(); await assert.rejects(f.runtime.archiveUnknown(), /history is full/);
  assert.deepEqual(f.checkpoint().history, [old, old, old, old]);
});
test("wrong identity or terminal status cannot enable existing-connection recovery", async () => {
  const f = await fixture(); await f.runtime.start(); await f.runtime.save({ phase: "unknown" });
  f.transport.lookup = async () => ({ pairing_id: uuid(), device_id: f.review.device_id, status: "REQUESTED" });
  await assert.rejects(f.runtime.inspectUnknown(), /does not match/);
  f.transport.lookup = async () => ({ pairing_id: f.review.pairing_id, device_id: f.review.device_id, status: "COMPLETED" });
  assert.equal((await f.runtime.inspectUnknown()).can_resume_existing, false); await assert.rejects(f.runtime.resumeExisting(), /Inspect/);
});

test("Pause during the first permission await preserves proven-unsent idle and sends nothing", async () => {
  const f = await fixture(); let release, entered;
  const waiting = new Promise((resolve) => { entered = resolve; });
  f.runtime.checkPermission = async () => { entered(); await new Promise((resolve) => { release = resolve; }); };
  const start = f.runtime.start(); await waiting;
  const paused = await f.runtime.cancel(); assert.equal(paused.phase, "idle"); assert.equal(f.checkpoint(), null);
  release(); await assert.rejects(start, /unavailable/);
  assert.equal(f.runtime.status().phase, "idle"); assert.deepEqual(f.calls, []); assert.equal(f.runtime.valid(f.runtime.state), true);
  f.runtime.checkPermission = async () => undefined;
  await f.runtime.start(); assert.deepEqual(f.calls, ["prepare"]);
});
