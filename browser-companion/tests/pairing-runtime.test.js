import assert from "node:assert/strict";
import test from "node:test";
import { PairingRuntime, pairingReadTimingSupport } from "../extension/pairing-runtime.js";
import { canonical, digest } from "../extension/protocol.js";

const uuid = () => crypto.randomUUID();

test("popup timing support accepts only finite numeric fields and cannot expose arbitrary diagnostic content", () => {
  const valid = { version: 1, issued_at_ms: 1800000000000, issued_age_ms: 6000, elapsed_ms: 989, http_status: 403 };
  assert.match(pairingReadTimingSupport(valid), /HW-COMPANION-READ-TIMING.*6000 ms.*989 ms.*403/);
  assert.match(pairingReadTimingSupport(valid), /Signed issued_at_ms: 1800000000000/);
  assert.match(pairingReadTimingSupport({ ...valid, issued_age_ms: -7 }), /-7 ms/);
  assert.match(pairingReadTimingSupport({ version: 1, issued_at_ms: null, issued_age_ms: null, elapsed_ms: null, http_status: null }), /unavailable/);
  for (const value of [null, [], {}, { ...valid, raw: "synthetic-private" }, { ...valid, request_id: "synthetic-private" },
    { ...valid, issued_age_ms: "synthetic-private" }, { ...valid, elapsed_ms: Infinity }, { ...valid, elapsed_ms: -1 },
    { ...valid, http_status: 0 }, { ...valid, version: 2 }, { ...valid, issued_age_ms: 86400001 }]) {
    assert.equal(pairingReadTimingSupport(value), null);
  }
  const hostile = { ...valid };
  Object.defineProperty(hostile, "issued_age_ms", { enumerable: true, get() { throw new Error("synthetic-private"); } });
  assert.equal(pairingReadTimingSupport(hostile), null);
  for (const issued_at_ms of [0, -1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, "synthetic-private"])
    assert.equal(pairingReadTimingSupport({ ...valid, issued_at_ms }), null);
});
async function fixture(stored = null, configChanges = {}) {
  let checkpoint = stored, permission = true, clock = Date.now();
  const calls = [];
  const configuration = { websiteOrigin: "https://www.hirewizhq.com", extensionId: "a".repeat(32), executorRevision: "owned_test", authorityKey: { public: "synthetic_test_only" }, ...configChanges };
  const review = { pairing_id: uuid(), device_id: uuid(), key_sha256: "a".repeat(64), request_sha256: "b".repeat(64), expires_at_ms: clock + 120000 };
  review.review_url = `${configuration.websiteOrigin}/browser-companion?pairing=${review.pairing_id}`;
  const transport = { configuration, claim: null,
    keyDigest: async () => "a".repeat(64),
    prepare: async ({ onPrepared, beforeCreate }) => { calls.push("prepare"); await onPrepared(review); await beforeCreate(); return review; },
    inspectKey: async () => { calls.push("inspect-key"); return { protocol_version: 2, status: "UNCLAIMED",
      key_sha256: "a".repeat(64), extension_id: configuration.extensionId, executor_revision: configuration.executorRevision }; },
    lookup: async () => { calls.push("status"); return { pairing_id: review.pairing_id, device_id: review.device_id, status: "CANDIDATE_CONFIRMED" }; },
    identify: async (refresh) => { calls.push(refresh ? "refresh" : "identify"); transport.claim = { payload: { expires_at_ms: Date.now() + 300000 } }; return { paired: true, allowed_actions: [] }; },
  };
  const store = { read: async () => structuredClone(checkpoint), write: async (value) => { checkpoint = structuredClone(value); } };
  const checkPermission = async () => { if (!permission) throw new Error("Permission revoked"); };
  const runtime = await new PairingRuntime({ transport, store, checkPermission, now: () => clock }).initialise();
  return { runtime, transport, calls, review, store, now: () => clock, advance: (milliseconds) => { clock += milliseconds; },
    checkpoint: () => structuredClone(checkpoint), revoke: () => { permission = false; } };
}

test("local popup status forwards timing without saving it or changing any connection authority", async () => {
  const f = await fixture(); await f.runtime.start();
  const before = f.checkpoint();
  f.transport.readTiming = () => ({ version: 1, issued_at_ms: 1800000000000, issued_age_ms: 6000, elapsed_ms: 989, http_status: 403 });
  const status = f.runtime.status();
  assert.equal(status.transport_timing.issued_age_ms, 6000);
  assert.equal(status.phase, "awaiting_candidate"); assert.equal(status.connected, false);
  assert.equal(status.can_archive_restart, false); assert.deepEqual(status.allowed_actions, []);
  assert.deepEqual(f.calls, ["prepare"]); assert.deepEqual(f.checkpoint(), before);
  assert.equal(JSON.stringify(before).includes("transport_timing"), false);
  assert.equal(JSON.stringify(before).includes("issued_at_ms"), false);
  await assert.rejects(f.runtime.start(), /blocked/);
  const reloaded = await fixture(before);
  assert.equal("transport_timing" in reloaded.runtime.status(), false);
  assert.deepEqual(reloaded.checkpoint(), before);
  f.transport.readTiming = () => { throw new Error("synthetic-private-diagnostic"); };
  assert.equal("transport_timing" in f.runtime.status(), false);
  assert.deepEqual(f.checkpoint(), before);
});

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
  const old = f.checkpoint().history[0], history = Array.from({ length: 64 }, () => structuredClone(old));
  await f.runtime.save({ history });
  const full = f.checkpoint();
  await f.runtime.inspectUnknown(); await assert.rejects(f.runtime.archiveUnknown(), /history is full/);
  assert.deepEqual(f.checkpoint(), full); assert.deepEqual(f.checkpoint().history, history);
  await assert.rejects(f.runtime.save({ history: [...history, old] }), /checkpoint is invalid/);
  assert.deepEqual(f.checkpoint(), full);
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

async function expiredCreation({ paused = false, unknown = false } = {}) {
  const f = await fixture(); await f.runtime.start(); f.advance(120001);
  if (paused) await f.runtime.cancel();
  if (unknown) await f.runtime.save({ phase: "unknown" });
  f.transport.lookup = async () => { f.calls.push("status"); return {
    pairing_id: f.review.pairing_id, device_id: f.review.device_id, status: "EXPIRED" }; };
  return f;
}

for (const phase of ["awaiting_candidate", "paused", "unknown"]) {
  test(`${phase}: expired known creation needs independent signed reads, explicit archive and separate new Start`, async () => {
    const f = await expiredCreation({ paused: phase === "paused", unknown: phase === "unknown" });
    const original = f.checkpoint(); const { history, ...record } = original;
    assert.equal(f.runtime.status().can_inspect_connection, true);
    await assert.rejects(f.runtime.archiveUnknown(), /Inspect/);
    const inspected = await f.runtime.inspectUnknown();
    assert.equal(inspected.phase, phase); assert.equal(inspected.observed_status, "EXPIRED");
    assert.equal(inspected.can_archive_restart, true); assert.equal(inspected.can_resume_existing, false);
    assert.deepEqual(f.checkpoint(), original); assert.deepEqual(f.calls, ["prepare", "status", "inspect-key"]);
    await assert.rejects(f.runtime.start(), /blocked/);
    const archived = await f.runtime.archiveUnknown();
    assert.equal(archived.phase, "idle"); assert.equal(archived.connected, false);
    const { reconciliation, ...saved } = f.checkpoint().history.at(-1);
    assert.deepEqual(saved, record); assert.deepEqual(f.checkpoint().history.slice(0, -1), history);
    assert.equal(reconciliation.status, "EXPIRED"); assert.equal(reconciliation.key_status, "UNCLAIMED");
    for (const key of ["pairing_id", "device_id", "key_sha256", "request_sha256", "binding", "expires_at_ms"]) assert.equal(reconciliation[key], record[key]);
    assert.deepEqual(f.calls, ["prepare", "status", "inspect-key", "status", "inspect-key"]);
    const fresh = await new PairingRuntime({ transport: f.transport, store: f.store,
      checkPermission: async () => undefined, now: f.now }).initialise();
    assert.equal(fresh.status().phase, "idle"); assert.equal(await f.transport.keyDigest(), record.key_sha256);
    await assert.rejects(fresh.archiveUnknown(), /Inspect/);
    const oldRequest = f.review.pairing_id;
    Object.assign(f.review, { pairing_id: uuid(), device_id: uuid(), request_sha256: "c".repeat(64), expires_at_ms: f.now() + 120000 });
    f.review.review_url = `${f.transport.configuration.websiteOrigin}/browser-companion?pairing=${f.review.pairing_id}`;
    assert.notEqual(f.review.pairing_id, oldRequest);
    assert.equal((await fresh.start()).phase, "awaiting_candidate");
    assert.equal(f.checkpoint().key_sha256, record.key_sha256); assert.equal(f.checkpoint().history.length, 1);
    assert.equal(f.checkpoint().history[0].pairing_id, oldRequest);
    assert.deepEqual(f.calls, ["prepare", "status", "inspect-key", "status", "inspect-key", "prepare"]);
  });
}

for (const status of ["REQUESTED", "CANDIDATE_CONFIRMED", "COMPLETED", "ACTIVE", "REVOKED", "UNKNOWN"]) {
  test(`known ${status} never authorizes archival or another request`, async () => {
    const f = await fixture(); await f.runtime.start(); const original = f.checkpoint();
    f.transport.lookup = async () => { f.calls.push("status"); return { pairing_id: f.review.pairing_id, device_id: f.review.device_id, status }; };
    assert.equal((await f.runtime.inspectUnknown()).can_archive_restart, false);
    await assert.rejects(f.runtime.archiveUnknown(), /Inspect/); await assert.rejects(f.runtime.start(), /blocked/);
    assert.deepEqual(f.checkpoint(), original); assert.deepEqual(f.calls, ["prepare", "status"]);
  });
}

test("expired previously candidate-confirmed creation stays terminal; no prior confirmation is adopted", async () => {
  const f = await expiredCreation();
  // PairingService.status maps an expired REQUESTED or CANDIDATE_CONFIRMED
  // request to EXPIRED; both device_challenge and complete_device still require
  // _request's live TTL. Archive preserves history and grants no confirmation.
  const native = { state: "CANDIDATE_CONFIRMED", expires_at_ms: f.review.expires_at_ms };
  f.transport.lookup = async () => { f.calls.push("status"); return { pairing_id: f.review.pairing_id,
    device_id: f.review.device_id, status: f.now() >= native.expires_at_ms ? "EXPIRED" : native.state }; };
  await f.runtime.inspectUnknown(); await f.runtime.archiveUnknown();
  assert.equal(native.state, "CANDIDATE_CONFIRMED"); assert.equal(f.runtime.status().phase, "idle");
  assert.deepEqual(f.runtime.status().allowed_actions, []);
  assert.deepEqual(f.calls, ["prepare", "status", "inspect-key", "status", "inspect-key"]);
});

for (const change of ["pairing", "device", "key", "key-response", "authority", "epoch-unavailable", "claimed", "unavailable"]) {
  test(`expired inspection ${change} drift or denial cannot reset retained state`, async () => {
    const f = await expiredCreation(); const original = f.checkpoint();
    const lookup = f.transport.lookup, key = f.transport.inspectKey;
    if (change === "pairing" || change === "device") f.transport.lookup = async () => ({ ...await lookup(), [change === "pairing" ? "pairing_id" : "device_id"]: uuid() });
    if (change === "key") f.transport.keyDigest = async () => "c".repeat(64);
    if (change === "key-response") f.transport.inspectKey = async () => ({ ...await key(), key_sha256: "c".repeat(64) });
    if (change === "authority") f.transport.inspectKey = async () => { const result = await key(); f.transport.configuration.authorityKey = { public: "different" }; return result; };
    if (change === "claimed") f.transport.inspectKey = async () => ({ ...await key(), status: "CLAIMED" });
    if (change === "unavailable" || change === "epoch-unavailable") f.transport.lookup = async () => { f.calls.push("status"); throw new Error(change === "epoch-unavailable" ? "Native authority epoch changed" : "Signed status unavailable"); };
    await assert.rejects(f.runtime.inspectUnknown());
    assert.equal(f.runtime.status().can_archive_restart, false);
    await assert.rejects(f.runtime.archiveUnknown(), /Inspect/); await assert.rejects(f.runtime.start(), /blocked/);
    assert.deepEqual(f.checkpoint(), original);
    assert.ok(f.calls.slice(1).every((call) => ["status", "inspect-key"].includes(call)));
  });
}

for (const change of ["claimed", "confirmed", "key", "permission", "unknown", "changed-state"]) {
  test(`archive rechecks expiry and key; later ${change} invalidates the earlier display proof`, async () => {
    const f = await expiredCreation(); await f.runtime.inspectUnknown(); const original = f.checkpoint();
    const key = f.transport.inspectKey;
    if (change === "claimed") f.transport.inspectKey = async () => ({ ...await key(), status: "CLAIMED" });
    if (change === "confirmed") f.transport.lookup = async () => ({ pairing_id: f.review.pairing_id, device_id: f.review.device_id, status: "CANDIDATE_CONFIRMED" });
    if (change === "key") f.transport.keyDigest = async () => "c".repeat(64);
    if (change === "permission") f.revoke();
    if (change === "unknown") f.transport.lookup = async () => { throw new Error("Exact expiry no longer available"); };
    if (change === "changed-state") f.transport.inspectKey = async () => { const value = await key(); await f.runtime.save({ request_sha256: "d".repeat(64) }); return value; };
    await assert.rejects(f.runtime.archiveUnknown());
    assert.equal(f.runtime.status().can_archive_restart, false); await assert.rejects(f.runtime.start(), /blocked/);
    assert.equal(f.checkpoint().history.length, original.history.length);
    assert.equal(f.checkpoint().pairing_id, original.pairing_id);
    assert.ok(f.calls.slice(1).every((call) => ["status", "inspect-key"].includes(call)));
  });
}

test("unknown complete and refresh outcomes never gain expired-creation recovery", async () => {
  for (const operation of ["complete", "refresh"]) {
    const f = await fixture(); await f.runtime.start(); if (operation === "refresh") await f.runtime.complete();
    f.transport.identify = async () => { throw new Error("Unknown completion"); };
    await assert.rejects(operation === "complete" ? f.runtime.complete() : f.runtime.refresh(), /needs inspection/);
    f.advance(120001); f.transport.lookup = async () => ({ pairing_id: f.review.pairing_id, device_id: f.review.device_id, status: "EXPIRED" });
    const original = f.checkpoint(); assert.equal((await f.runtime.inspectUnknown()).can_archive_restart, false);
    await assert.rejects(f.runtime.archiveUnknown(), /Inspect/); assert.deepEqual(f.checkpoint(), original);
  }
});

test("expired recovery does not discard full history or accept a changed retained proof on reload", async () => {
  const f = await expiredCreation(); const { history, ...record } = f.checkpoint(); assert.deepEqual(history, []);
  const retained = Array.from({ length: 64 }, () => structuredClone(record));
  await f.runtime.save({ history: retained }); const full = f.checkpoint();
  await f.runtime.inspectUnknown(); await assert.rejects(f.runtime.archiveUnknown(), /history is full/);
  assert.equal(f.checkpoint().history.length, 64); assert.deepEqual(f.checkpoint(), full);
  const g = await expiredCreation(); await g.runtime.inspectUnknown(); await g.runtime.archiveUnknown();
  const tampered = g.checkpoint(); tampered.history[0].reconciliation.device_id = uuid();
  await g.store.write(tampered);
  await assert.rejects(new PairingRuntime({ transport: g.transport, store: g.store, checkPermission: async () => undefined, now: g.now }).initialise(), /Stored connection state/);
});
