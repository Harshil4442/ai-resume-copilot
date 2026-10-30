import assert from "node:assert/strict";
import test from "node:test";
import { LostKeyRecovery, LEGACY_KEYS, RECOVERY_UNAVAILABLE, RecoveryGate, recoveryKey } from "../extension/lost-key-recovery.js";
const id = "12345678-1234-1234-1234-123456789abc";
const old = { pairing_checkpoint: { device_id: id, phase: "unknown", send_kind: "complete", private_candidate_text: "preserve-without-rendering" },
  assistance_checkpoint: { device_id: id, phase: "unknown", application_id: "old-application", review_id: id },
  native_application_evidence: { [`submit:${id}`]: { outcome: "unknown", proof: "exact-retained-proof" } } };
const clone = (value) => structuredClone(value);

class MemoryJournal {
  constructor() { this.record = null; this.legacyMissing = true; this.identity = null; this.reservations = 0; }
  async read() { return { record: clone(this.record), legacyMissing: this.legacyMissing }; }
  async reserve(record) {
    if (this.record || !this.legacyMissing) throw new Error(RECOVERY_UNAVAILABLE);
    this.record = clone(record); this.reservations++; return clone(record);
  }
  async activate(expected, identity) {
    if (this.record?.id !== expected.id || this.record.phase !== "pending" || !this.legacyMissing || this.identity) throw new Error(RECOVERY_UNAVAILABLE);
    this.identity = identity; this.record.phase = "active"; return clone(this.record);
  }
  async block(expected) {
    assert.equal(this.record.id, expected.id); this.record.phase = "blocked"; return clone(this.record);
  }
}
function setup({ createIdentity, journal = new MemoryJournal() } = {}) {
  const value = { saved: clone(old), now: 1800000000000, generated: 0, journal };
  value.make = () => new LostKeyRecovery({ journal, now: () => value.now, readLegacy: async () => clone(value.saved),
    createIdentity: async () => { value.generated++; return createIdentity ? createIdentity(value) : { device_id: crypto.randomUUID(), synthetic_identity: true }; } });
  value.recovery = value.make(); return value;
}
const confirm = (review) => ({ recovery_id: review.recovery_id, acknowledged: true });
const refuses = (operation) => assert.rejects(operation, new RegExp(RECOVERY_UNAVAILABLE.replaceAll(".", "\\.")));

test("review preserves all old records, generates nothing and reveals only bounded public metadata", async () => {
  const f = setup(), review = await f.recovery.review();
  assert.equal(f.generated, 0); assert.equal(f.journal.record, null); assert.deepEqual(f.saved, old);
  assert.deepEqual(review.old_device_ids, [id]); assert.deepEqual(review.application_actions, []);
  assert.equal(review.no_old_device_revocation_proven, true);
  assert.equal(JSON.stringify(review).includes("preserve-without-rendering"), false);
  assert.equal(JSON.stringify(review).includes("exact-retained-proof"), false);
  assert.match(review.warning, /automatic retry and another identity are unsupported/);
});

test("confirmation, exact review ID, freshness and missing-key preconditions precede any reservation or generation", async (t) => {
  for (const change of ["unchecked", "different_review", "expired", "clock_back", "key_appeared", "no_review"]) await t.test(change, async () => {
    const f = setup(), review = await f.recovery.review(), message = confirm(review);
    if (change === "unchecked") message.acknowledged = false;
    if (change === "different_review") message.recovery_id = crypto.randomUUID();
    if (change === "expired") f.now += 300001;
    if (change === "clock_back") f.now--;
    if (change === "key_appeared") f.journal.legacyMissing = false;
    if (change === "no_review") f.recovery.reviewed = null;
    await refuses(() => f.recovery.commit(message));
    assert.equal(f.generated, 0); assert.equal(f.journal.record, null); assert.deepEqual(f.saved, old);
  });
});

test("old snapshots changing after review cannot be accepted or overwritten", async (t) => {
  for (const key of LEGACY_KEYS) await t.test(key, async () => {
    const f = setup(), review = await f.recovery.review();
    f.saved[key] = { changed: true };
    await refuses(() => f.recovery.commit(confirm(review)));
    assert.equal(f.generated, 0); assert.equal(f.journal.record, null); assert.deepEqual(f.saved[key], { changed: true });
  });
});

test("only one concurrent confirmed review can reserve and generate a separate identity", async () => {
  const f = setup(), other = f.make(), [one, two] = await Promise.all([f.recovery.review(), other.review()]);
  const results = await Promise.allSettled([f.recovery.commit(confirm(one)), other.commit(confirm(two))]);
  assert.equal(results.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(f.generated, 1); assert.equal(f.journal.reservations, 1); assert.equal(f.journal.record.phase, "active");
  assert.deepEqual(f.saved, old); assert.deepEqual(f.journal.record.snapshot, old);
  assert.notEqual(f.journal.identity.device_id, id);
});

test("fresh active context namespaces all three stores and inherits no old IDs or approvals", async () => {
  const f = setup(), review = await f.recovery.review(), result = await f.recovery.commit(confirm(review));
  const context = await f.recovery.context();
  assert.equal(context.slot, `device-recovery:${result.recovery_id}`);
  const names = LEGACY_KEYS.map((name) => recoveryKey(context, name));
  assert.equal(new Set(names).size, 3);
  for (const [index, name] of names.entries()) {
    assert.notEqual(name, LEGACY_KEYS[index]); assert.equal(Object.hasOwn(f.saved, name), false);
  }
  assert.deepEqual(f.journal.record.snapshot, old); assert.deepEqual(f.saved, old);
  assert.deepEqual(result.application_actions, []); assert.equal(result.no_old_device_revocation_proven, true);
  await refuses(() => f.recovery.review()); assert.equal(f.generated, 1);
});

test("reservation acknowledgement loss leaves pending quarantine and prevents any automatic generation or repeated recovery", async () => {
  const f = setup(), original = f.journal.reserve.bind(f.journal), review = await f.recovery.review();
  f.journal.reserve = async (value) => { await original(value); throw new Error("lost local acknowledgement"); };
  await assert.rejects(f.recovery.commit(confirm(review)));
  assert.equal(f.journal.record.phase, "pending"); assert.equal(f.generated, 0); assert.deepEqual(f.journal.record.snapshot, old);
  const restarted = f.make(); await refuses(() => restarted.context()); await refuses(() => restarted.review());
  assert.equal((await restarted.inspect()).phase, "pending"); assert.equal(f.generated, 0);
});

test("generation or activation interruption remains pending, and a restart never generates another identity", async (t) => {
  for (const point of ["generation", "activation"]) await t.test(point, async () => {
    const f = setup({ createIdentity: point === "generation" ? () => { throw new Error("synthetic failure"); } : undefined });
    if (point === "activation") f.journal.activate = async () => { throw new Error("synthetic IDB interruption"); };
    const review = await f.recovery.review(); await assert.rejects(f.recovery.commit(confirm(review)));
    assert.equal(f.journal.record.phase, "pending"); assert.equal(f.generated, 1); assert.deepEqual(f.journal.record.snapshot, old);
    const restarted = f.make(); await refuses(() => restarted.context()); await refuses(() => restarted.review());
    assert.equal((await restarted.inspect()).phase, "pending"); assert.equal(f.generated, 1);
  });
});

test("a legacy key appearing during generation prevents activation without overwriting it", async () => {
  const f = setup({ createIdentity: (state) => { state.journal.legacyMissing = false; return { synthetic_identity: true }; } });
  const review = await f.recovery.review(); await refuses(() => f.recovery.commit(confirm(review)));
  assert.equal(f.journal.record.phase, "pending"); assert.equal(f.journal.identity, null); assert.equal(f.journal.legacyMissing, false);
  assert.deepEqual(f.saved, old); assert.equal(f.generated, 1);
});

test("drift while generating blocks recovery and preserves the archived exact unknown outcomes", async () => {
  const f = setup({ createIdentity: (state) => { state.saved.assistance_checkpoint = { changed: true }; return {}; } });
  const review = await f.recovery.review(); await refuses(() => f.recovery.commit(confirm(review)));
  assert.equal(f.journal.record.phase, "blocked"); assert.equal(f.journal.identity, null);
  assert.deepEqual(f.journal.record.snapshot, old); assert.deepEqual(f.saved.assistance_checkpoint, { changed: true });
});

test("post-activation drift remains permanently blocked even if the old snapshot is later restored", async () => {
  const f = setup(), review = await f.recovery.review(); await f.recovery.commit(confirm(review));
  f.saved.native_application_evidence = { changed: true }; await refuses(() => f.recovery.context());
  assert.equal(f.journal.record.phase, "blocked"); f.saved = clone(old);
  await refuses(() => f.recovery.context()); await refuses(() => f.make().review());
  assert.equal(f.generated, 1); assert.deepEqual(f.journal.record.snapshot, old);
});

test("inspection-observed drift latches a protective block without generating or changing archived data", async () => {
  const f = setup(), review = await f.recovery.review(); await f.recovery.commit(confirm(review));
  f.saved.pairing_checkpoint = { changed: true };
  assert.equal((await f.recovery.inspect()).phase, "blocked");
  assert.equal(f.journal.record.phase, "blocked"); f.saved = clone(old);
  await refuses(() => f.recovery.context()); assert.equal(f.generated, 1);
  assert.deepEqual(f.journal.record.snapshot, old);
});

test("legacy-key appearance after atomic activation is caught by the final journal barrier", async () => {
  const f = setup(), activate = f.journal.activate.bind(f.journal), review = await f.recovery.review();
  f.journal.activate = async (...args) => { const value = await activate(...args); f.journal.legacyMissing = false; return value; };
  await refuses(() => f.recovery.commit(confirm(review)));
  assert.equal(f.journal.record.phase, "blocked"); assert.equal(f.generated, 1);
  assert.deepEqual(f.saved, old); assert.deepEqual(f.journal.record.snapshot, old);
});

test("oversized, absent or corrupted archives refuse instead of trimming evidence or falling back", async (t) => {
  for (const kind of ["oversized", "absent", "corrupt"]) await t.test(kind, async () => {
    const f = setup();
    if (kind === "oversized") f.saved.native_application_evidence = { value: "x".repeat(384 * 1024) };
    if (kind === "absent") f.saved.pairing_checkpoint = null;
    if (kind === "corrupt") f.journal.record = { phase: "active", snapshot: old };
    await refuses(() => f.recovery.review()); assert.equal(f.generated, 0);
    if (kind === "corrupt") await refuses(() => f.recovery.context());
  });
});

test("old-device navigation metadata admits saved UUIDs only and never a URL or a revocation claim", async () => {
  const f = setup(); f.saved.pairing_checkpoint.device_id = "https://attacker.invalid/device";
  const review = await f.recovery.review(); assert.deepEqual(review.old_device_ids, [id]);
  assert.equal(review.no_old_device_revocation_proven, true);
  assert.equal(JSON.stringify(review).includes("attacker.invalid"), false);
});

test("recovery gate excludes competing operations without serializing ordinary pause/cancel", async () => {
  const gate = new RecoveryGate(); let finish;
  const running = gate.run(false, () => new Promise((resolve) => { finish = resolve; }));
  assert.equal(await gate.run(false, async () => "pause remains concurrent"), "pause remains concurrent");
  await refuses(() => gate.run(true, async () => assert.fail("Cannot recover during an operation")));
  finish(); await running;
  await gate.run(true, async () => {
    await refuses(() => gate.run(false, async () => assert.fail("No ordinary operation during recovery")));
    await refuses(() => gate.run(true, async () => assert.fail("No competing recovery")));
  });
  await assert.rejects(gate.run(true, async () => { throw new Error("interrupted"); }));
  assert.equal(await gate.run(false, async () => "released"), "released");
});
