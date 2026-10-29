import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { showStartupFailure, startupDiagnostic, startupStatus, STARTUP_STATUS_TIMEOUT_MS } from "../extension/startup-diagnostics.js";

const html = await readFile(new URL("../extension/popup.html", import.meta.url), "utf8");
const code = (kind) => `HW-COMPANION-START-${kind}`;
const secret = "sk-synthetic-secret@example.invalid https://credentials.invalid/token?key=private";
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function fakeDocument() {
  const elements = new Map();
  for (const match of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    elements.set(match[3], {
      tag: match[1], hidden: /\bhidden\b/.test(match[2]), disabled: /\bdisabled\b/.test(match[2]),
      checked: false, value: "", textContent: "", addEventListener() {}, append() {}, replaceChildren() {},
    });
  }
  return {
    getElementById: (id) => elements.get(id),
    querySelectorAll: () => [...elements.values()].filter((item) => ["button", "input", "select", "textarea"].includes(item.tag)),
    elements,
  };
}

function assertLocked(document, kind) {
  assert.equal(document.getElementById("mode").textContent, "Browser connection unavailable. No application action is enabled.");
  assert.equal(document.getElementById("error").hidden, false);
  assert.match(document.getElementById("error").textContent, new RegExp(`Support code: ${code(kind)}$`));
  assert.ok(document.querySelectorAll().every((item) => item.disabled));
  for (const id of ["fixture-connection", "fixture-prepare", "native-connection", "assistance", "review"]) {
    assert.equal(document.getElementById(id).hidden, true, id);
  }
}

async function captureFailure(operation) {
  try { await operation(); } catch (failure) { return failure; }
  assert.fail("Expected a locked startup failure");
}

test("one existing status message returns an unchanged successful value", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [], value = { mode: "reviewed_assistance", native_pairing: true, connection: { phase: "paired" } };
  assert.equal(await startupStatus(async (message) => { calls.push(message); return { ok: true, value }; }), value);
  t.mock.timers.tick(STARTUP_STATUS_TIMEOUT_MS + 1);
  assert.deepEqual(calls, [{ type: "status" }]);
});

test("known initializer messages map to finite categories without retaining the raw failure", async (t) => {
  const cases = [
    ["Reviewed browser connection configuration is unavailable", "CONFIG"],
    ["Reviewed production pairing configuration is unavailable", "CONFIG"],
    ["Reviewed assistance configuration is unavailable", "CONFIG"],
    ["Device key storage is unavailable", "KEY"],
    ["Stored connection state needs inspection; starting again is blocked", "CHECKPOINT"],
    ["The saved connection belongs to a different retained device key", "CHECKPOINT"],
    ["Connection checkpoint is invalid", "CHECKPOINT"],
    ["Stored assistance state needs inspection. No field action is enabled.", "CHECKPOINT"],
    ["Retained native evidence needs inspection.", "CHECKPOINT"],
    ["Connection storage is unavailable", "STORAGE"],
  ];
  for (const [message, kind] of cases) await t.test(kind + ": " + message, async () => {
    let calls = 0;
    const failure = await captureFailure(() => startupStatus(async (request) => {
      assert.deepEqual(request, { type: "status" }); calls++;
      return { ok: false, error: message };
    }));
    assert.equal(calls, 1);
    assert.equal(failure.message, "Companion startup could not be verified");
    assert.equal(startupDiagnostic(failure, "0.8.4").code, code(kind));
    assert.equal("cause" in failure, false);
  });
});

test("exact Chrome missing-worker and closed-channel messages are recognized", async (t) => {
  for (const message of [
    "Companion worker unavailable. Inspect the portal before retrying.",
    "Could not establish connection. Receiving end does not exist.",
    "The message port closed before a response was received.",
    "A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received",
  ]) await t.test(message, async () => {
    const failure = await captureFailure(() => startupStatus(async () => { throw new Error(message); }));
    assert.equal(startupDiagnostic(failure, "0.8.4").code, code("WORKER"));
  });
});

test("unknown secrets, prefixed/suffixed known strings, objects and hostile getters never reach the UI", async (t) => {
  for (const thrown of [
    new Error(secret),
    new Error("Device key storage is unavailable " + secret),
    new Error(secret + " Device key storage is unavailable"),
    secret,
    { message: { toString() { throw new Error(secret); } }, stack: secret },
    { get message() { throw new Error(secret); } },
  ]) await t.test(typeof thrown === "string" ? "raw string" : "unknown object", async () => {
    const failure = await captureFailure(() => startupStatus(async () => { throw thrown; }));
    const document = fakeDocument();
    showStartupFailure(document, failure, "0.8.4");
    assertLocked(document, "UNKNOWN");
    const visible = document.getElementById("error").textContent;
    assert.equal(visible.includes("synthetic-secret"), false);
    assert.equal(visible.includes("credentials.invalid"), false);
    assert.equal(visible.includes("Device key storage is unavailable"), false);
  });
});

test("malformed or absent worker responses remain locked without rendering their contents", async (t) => {
  for (const [response, kind] of [[undefined, "WORKER"], [null, "WORKER"], [{ ok: false }, "WORKER"],
    [{ ok: false, error: secret }, "UNKNOWN"], [{ ok: false, error: { message: secret } }, "UNKNOWN"],
    [{ ok: 1, error: secret }, "UNKNOWN"]]) await t.test(kind, async () => {
    const failure = await captureFailure(() => startupStatus(async () => response));
    const diagnostic = startupDiagnostic(failure, "0.8.4");
    assert.equal(diagnostic.code, code(kind));
    assert.equal(JSON.stringify(diagnostic).includes(secret), false);
  });
});

test("only a numeric Chrome manifest version is shown", () => {
  for (const version of [undefined, null, {}, "0.8.4 " + secret, "<img src=x>", "0.8.4\n", "123456.1"]) {
    assert.equal(startupDiagnostic(new Error(secret), version).version, "unavailable");
  }
  assert.equal(startupDiagnostic(new Error(secret), "0.8.4").version, "0.8.4");
});

test("key phases require both the exact existing key error and a finite phase value", async (t) => {
  for (const phase of ["OPEN-BLOCKED", "OPEN-ERROR", "OPEN-TIMEOUT", "TX-ERROR", "TX-ABORT", "TX-TIMEOUT", "MISSING-RETAINED", "IDENTITY-SHAPE", "KEY-TYPE", "KEY-ALGORITHM", "KEY-USAGES", "PUBLIC-SHAPE", "FINGERPRINT", "PUBLIC-IMPORT", "PROOF-SIGN", "PROOF-VERIFY"]) {
    const failure = await captureFailure(() => startupStatus(async () => ({ ok: false, error: "Device key storage is unavailable", device_phase: phase })));
    const document = fakeDocument(); showStartupFailure(document, failure, "0.8.4");
    assertLocked(document, `KEY-${phase}`);
  }
  for (const [error, phase, expected] of [
    ["Device key storage is unavailable", secret, "KEY"],
    ["Device key storage is unavailable", "PROOF-SIGN " + secret, "KEY"],
    ["Device key storage is unavailable", { toString() { throw new Error(secret); } }, "KEY"],
    [secret, "PROOF-SIGN", "UNKNOWN"],
    ["Device key storage is unavailable " + secret, "PROOF-SIGN", "UNKNOWN"],
  ]) await t.test(expected, async () => {
    const failure = await captureFailure(() => startupStatus(async () => ({ ok: false, error, device_phase: phase })));
    const document = fakeDocument(); showStartupFailure(document, failure, "0.8.4");
    assertLocked(document, expected);
    assert.equal(document.getElementById("error").textContent.includes("synthetic-secret"), false);
  });
});

test("failure hides already visible action regions and disables every existing control", () => {
  const document = fakeDocument();
  for (const element of document.elements.values()) { element.hidden = false; element.disabled = false; }
  showStartupFailure(document, new Error("Connection storage is unavailable"), "0.8.4");
  assertLocked(document, "STORAGE");
  assert.match(document.getElementById("error").textContent, /Installed version: 0\.8\.4/);
});

let popupCase = 0;
async function withPopup(document, runtime, run) {
  const saved = new Map(["document", "window", "chrome"].map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  globalThis.document = document;
  globalThis.window = { addEventListener() {} };
  // No storage, permissions, fetch or Chrome APIs are provided to this synthetic UI.
  globalThis.chrome = { runtime };
  try { await run(() => import(`../extension/popup.js?startup-test=${++popupCase}`)); }
  finally {
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test("actual popup renders only the finite worker response diagnostic and installed manifest version", async () => {
  const document = fakeDocument(), calls = [];
  await withPopup(document, {
    sendMessage: async (message) => { calls.push(message); return { ok: false, error: "Device key storage is unavailable" }; },
    getManifest: () => ({ version: "0.8.4" }),
  }, async (load) => { await load(); });
  assert.deepEqual(calls, [{ type: "status" }]);
  assertLocked(document, "KEY");
  assert.match(document.getElementById("error").textContent, /Installed version: 0\.8\.4/);
});

test("actual popup redacts unknown worker secrets and manifest failures", async () => {
  const document = fakeDocument();
  await withPopup(document, {
    sendMessage: async () => ({ ok: false, error: secret }),
    getManifest: () => { throw new Error(secret); },
  }, async (load) => { await load(); });
  assertLocked(document, "UNKNOWN");
  assert.match(document.getElementById("error").textContent, /Installed version: unavailable/);
  assert.equal(document.getElementById("error").textContent.includes(secret), false);
});

test("one 60-second presentation timeout cannot be undone by a late successful status or trigger another action", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const document = fakeDocument(), response = deferred(), called = deferred(), calls = [];
  await withPopup(document, {
    sendMessage: (message) => { calls.push(message); called.resolve(); return response.promise; },
    getManifest: () => ({ version: "0.8.4" }),
  }, async (load) => {
    const loaded = load();
    await called.promise;
    t.mock.timers.tick(STARTUP_STATUS_TIMEOUT_MS - 1);
    await Promise.resolve();
    assert.equal(document.getElementById("error").hidden, true);
    t.mock.timers.tick(1);
    await loaded;
    assertLocked(document, "TIMEOUT");
    const before = [...document.elements].map(([id, item]) => [id, item.hidden, item.disabled, item.textContent]);
    response.resolve({ ok: true, value: { mode: "local_fixture", connected: true } });
    await response.promise;
    await Promise.resolve(); await Promise.resolve();
    assert.deepEqual([...document.elements].map(([id, item]) => [id, item.hidden, item.disabled, item.textContent]), before);
    assert.deepEqual(calls, [{ type: "status" }]);
  });
});

test("a late status rejection after timeout is consumed without retry or raw error presentation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const response = deferred(); let calls = 0;
  const checked = startupStatus(() => { calls++; return response.promise; });
  const observed = captureFailure(() => checked);
  await Promise.resolve();
  t.mock.timers.tick(STARTUP_STATUS_TIMEOUT_MS);
  const failure = await observed;
  response.reject(new Error(secret));
  await Promise.resolve(); await Promise.resolve();
  assert.equal(startupDiagnostic(failure, "0.8.4").code, code("TIMEOUT"));
  assert.equal(calls, 1);
});
