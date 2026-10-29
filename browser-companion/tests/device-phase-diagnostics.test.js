// Synthetic IDB adapter and synthetic keys only; never an owner browser/profile.
import assert from "node:assert/strict";
import test from "node:test";
import { deviceFailurePhase, deviceIdentity } from "../extension/device.js";
import { canonical, digest } from "../extension/protocol.js";
import { CONFIG } from "../extension/config.js";

const generic = "Device key storage is unavailable", secret = "synthetic-private-failure-never-show";
const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
const public_key = await crypto.subtle.exportKey("jwk", pair.publicKey);
const retained = { device_id: crypto.randomUUID(), private_key: pair.privateKey, public_key,
  key_sha256: await digest(canonical({ kty: public_key.kty, crv: public_key.crv, x: public_key.x, y: public_key.y })) };

function idbFixture(t, { identity = retained, openFailure, txFailure } = {}) {
  const counts = { opens: 0, reads: 0, writes: 0, closes: 0, aborts: 0, modes: [] };
  let readStarted;
  counts.readStarted = new Promise((resolve) => { readStarted = resolve; });
  const database = {
    close() { counts.closes++; },
    transaction(store, mode) {
      assert.equal(store, "keys"); counts.modes.push(mode);
      if (txFailure === "unavailable") throw new Error(secret);
      const tx = {
        abort() { counts.aborts++; tx.onabort?.(); },
        objectStore(name) {
          assert.equal(name, "keys");
          return {
            get(key) {
              assert.equal(key, "device"); counts.reads++; readStarted();
              const read = { result: identity };
              queueMicrotask(() => {
                if (txFailure === "timeout") return;
                if (txFailure === "abort") { tx.onabort?.(); return; }
                if (txFailure === "error") { tx.onerror?.(); return; }
                read.onsuccess?.(); tx.oncomplete?.();
              });
              return read;
            },
            add() { counts.writes++; assert.fail("A diagnostic of retained state must never write or replace a key"); },
          };
        },
      };
      return tx;
    },
  };
  const previous = Object.getOwnPropertyDescriptor(globalThis, "indexedDB");
  Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: {
    open(name, version) {
      counts.opens++; assert.equal(name, "hirewiz-companion-device"); assert.equal(version, 1);
      if (openFailure === "unavailable") throw new Error(secret);
      const request = { result: database };
      queueMicrotask(() => {
        if (openFailure === "timeout") return;
        if (openFailure === "blocked") request.onblocked?.();
        else if (openFailure === "error") request.onerror?.();
        else request.onsuccess?.();
      });
      return request;
    },
  } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "indexedDB", previous);
    else delete globalThis.indexedDB;
  });
  return counts;
}

async function rejectPhase(phase, operation = () => deviceIdentity({ requireRetained: true })) {
  try { await operation(); assert.fail("Expected retained identity refusal"); }
  catch (failure) {
    assert.equal(failure.message, generic);
    assert.equal(deviceFailurePhase(failure), phase);
    assert.equal(JSON.stringify(failure).includes(secret), false);
    assert.deepEqual(Object.keys(failure), []);
    assert.equal("cause" in failure, false);
    return failure;
  }
}

test("IDB open failures expose only their existing fixed branch without key reads", async (t) => {
  for (const kind of ["blocked", "error", "unavailable"]) await t.test(kind, async (t) => {
    const counts = idbFixture(t, { openFailure: kind });
    await rejectPhase(`OPEN-${kind.toUpperCase()}`);
    assert.equal(counts.opens, 1); assert.equal(counts.reads, 0); assert.equal(counts.writes, 0);
  });
});

test("existing 3-second IDB open timeout is identified without retry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const counts = idbFixture(t, { openFailure: "timeout" });
  const pending = rejectPhase("OPEN-TIMEOUT");
  t.mock.timers.tick(3000); await pending;
  assert.equal(counts.opens, 1); assert.equal(counts.reads, 0); assert.equal(counts.writes, 0);
});

test("transaction failures keep exactly one existing read and never repair storage", async (t) => {
  for (const kind of ["abort", "error", "unavailable"]) await t.test(kind, async (t) => {
    const counts = idbFixture(t, { txFailure: kind });
    await rejectPhase(`TX-${kind.toUpperCase()}`);
    assert.equal(counts.opens, 1); assert.equal(counts.reads, kind === "unavailable" ? 0 : 1);
    assert.equal(counts.writes, 0); assert.equal(counts.closes, 1);
    assert.deepEqual(counts.modes, ["readonly"]);
  });
});

test("existing transaction timeout still aborts once and preserves retained state", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const counts = idbFixture(t, { txFailure: "timeout" });
  const pending = rejectPhase("TX-TIMEOUT");
  await counts.readStarted;
  t.mock.timers.tick(3000); await pending;
  assert.equal(counts.reads, 1); assert.equal(counts.aborts, 1); assert.equal(counts.writes, 0); assert.equal(counts.closes, 1);
});

test("missing required retained identity is distinguished without generating, exporting or writing a replacement", async (t) => {
  const counts = idbFixture(t, { identity: null });
  for (const name of ["generateKey", "exportKey", "sign", "verify"]) t.mock.method(crypto.subtle, name, () => assert.fail(`Unexpected ${name}`));
  await rejectPhase("MISSING-RETAINED");
  assert.equal(counts.reads, 1); assert.equal(counts.writes, 0); assert.equal(counts.closes, 1);
});

test("existing shape, algorithm, usage, public-key and fingerprint guards remain strict", async (t) => {
  const badAlgorithm = Object.create(CryptoKey.prototype);
  Object.defineProperties(badAlgorithm, { type: { value: "private" }, extractable: { value: false }, algorithm: { value: { name: "HMAC" } } });
  const badUsage = Object.create(CryptoKey.prototype);
  Object.defineProperties(badUsage, { type: { value: "private" }, extractable: { value: false }, algorithm: { value: { name: "ECDSA", namedCurve: "P-256" } }, usages: { value: [] } });
  for (const [identity, phase] of [
    [{ ...retained, extra: secret }, "IDENTITY-SHAPE"],
    [{ ...retained, private_key: {} }, "KEY-TYPE"],
    [{ ...retained, private_key: badAlgorithm }, "KEY-ALGORITHM"],
    [{ ...retained, private_key: badUsage }, "KEY-USAGES"],
    [{ ...retained, public_key: { ...public_key, d: secret } }, "PUBLIC-SHAPE"],
    [{ ...retained, key_sha256: "0".repeat(64) }, "FINGERPRINT"],
  ]) await t.test(phase, async (t) => {
    const counts = idbFixture(t, { identity });
    t.mock.method(crypto.subtle, "sign", () => assert.fail("No proof before the existing identity guards"));
    await rejectPhase(phase);
    assert.equal(counts.reads, 1); assert.equal(counts.writes, 0); assert.equal(counts.closes, 1);
  });
});

test("existing public import, sign and verification failures are separated without an extra proof", async (t) => {
  for (const [method, phase] of [["importKey", "PUBLIC-IMPORT"], ["sign", "PROOF-SIGN"], ["verify", "PROOF-VERIFY"]]) await t.test(phase, async (t) => {
    const counts = idbFixture(t); let attempts = 0;
    t.mock.method(crypto.subtle, method, async () => { attempts++; throw new Error(secret); });
    await rejectPhase(phase);
    assert.equal(attempts, 1); assert.equal(counts.reads, 1); assert.equal(counts.writes, 0);
  });
  await t.test("false proof", async (t) => {
    const counts = idbFixture(t); let attempts = 0;
    t.mock.method(crypto.subtle, "verify", async () => { attempts++; return false; });
    await rejectPhase("PROOF-VERIFY");
    assert.equal(attempts, 1); assert.equal(counts.reads, 1); assert.equal(counts.writes, 0);
  });
});

test("valid retained identity still uses exactly one read, digest, public import, sign and verify with no generation/export/write", async (t) => {
  const counts = idbFixture(t), operations = [];
  for (const name of ["digest", "importKey", "sign", "verify"]) {
    const original = crypto.subtle[name].bind(crypto.subtle);
    t.mock.method(crypto.subtle, name, (...args) => { operations.push(name); return original(...args); });
  }
  for (const name of ["generateKey", "exportKey"]) t.mock.method(crypto.subtle, name, () => assert.fail(`Unexpected ${name}`));
  const result = await deviceIdentity({ requireRetained: true });
  assert.equal(result.private_key, retained.private_key); assert.equal(result.device_id, retained.device_id);
  assert.deepEqual(operations, ["digest", "importKey", "sign", "verify"]);
  assert.equal(counts.opens, 1); assert.equal(counts.reads, 1); assert.equal(counts.writes, 0); assert.equal(counts.closes, 1);
});

test("plain or forged errors cannot supply trusted device phase metadata", () => {
  for (const failure of [null, undefined, secret, new Error(generic), { message: generic, device_phase: "PROOF-SIGN" }]) {
    assert.equal(deviceFailurePhase(failure), null);
  }
});

test("actual worker refuses an unavailable recovery journal only to the exact existing popup sender", async (t) => {
  const counts = idbFixture(t, { openFailure: "blocked" });
  let listener, localReads = 0;
  const previous = Object.getOwnPropertyDescriptor(globalThis, "chrome");
  Object.defineProperty(globalThis, "chrome", { configurable: true, value: {
    runtime: { id: CONFIG.extensionId, getURL: (path) => `chrome-extension://${CONFIG.extensionId}/${path}`, onMessage: { addListener: (value) => { listener = value; } } },
    storage: { local: { get: async (key) => { assert.equal(key, "pairing_checkpoint"); localReads++; return { pairing_checkpoint: {} }; } } },
  } });
  t.after(() => { if (previous) Object.defineProperty(globalThis, "chrome", previous); else delete globalThis.chrome; });
  await import("../extension/background.js?device-phase-synthetic-test");
  const popup = { id: CONFIG.extensionId, url: `chrome-extension://${CONFIG.extensionId}/popup.html` };
  assert.equal(listener({ type: "status" }, { ...popup, url: popup.url + "?other" }, () => assert.fail("Wrong sender must not be answered")), false);
  const reply = await new Promise((resolve) => { assert.equal(listener({ type: "status" }, popup, resolve), true); });
  assert.deepEqual(reply, { ok: false, error: "Local lost-key recovery needs inspection. No application action is enabled." });
  assert.equal(localReads, 0); assert.equal(counts.opens, 1); assert.equal(counts.reads, 0); assert.equal(counts.writes, 0);
  const otherReply = await new Promise((resolve) => listener({ type: "pairing_status" }, popup, resolve));
  assert.deepEqual(otherReply, { ok: false, error: "Local lost-key recovery needs inspection. No application action is enabled." });
  assert.equal(localReads, 0); assert.equal(counts.opens, 2);
  const invalidReply = await new Promise((resolve) => listener(null, popup, resolve));
  assert.deepEqual(invalidReply, { ok: false, error: "Unsupported assistance operation" });
});
