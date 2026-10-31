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
      tag: match[1], hidden: /\bhidden\b/.test(match[2]), _disabled: /\bdisabled\b/.test(match[2]),
      get disabled() { return this._disabled; }, set disabled(value) { this._disabled = Boolean(value); },
      checked: false, value: "", textContent: "", listeners: new Map(),
      addEventListener(type, listener) { this.listeners.set(type, listener); },
      append() {}, replaceChildren() {},
      querySelectorAll() { return [...elements.entries()].filter(([id, item]) => id.startsWith(match[3] + "-") && ["button", "input"].includes(item.tag)).map(([, item]) => item); },
    });
  }
  return {
    getElementById: (id) => elements.get(id),
    createElement: () => ({ textContent: "", append() {} }),
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


const activeAssistance = () => ({ phase: "active", busy: false, review_id: null,
  key_sha256: "a".repeat(64), filled_count: 0, can_fill: false, can_reconcile: false, native_upload: false });
const pairedConnection = () => ({ phase: "paired", busy: false, connected: true,
  key_sha256: "a".repeat(64), can_inspect_connection: false });
const currentConnectionError = "Check current native browser connection before application assistance";
async function assistancePopup(sendMessage, run, initial = activeAssistance()) {
  const document = fakeDocument(), calls = [];
  await withPopup(document, {
    sendMessage: async (message) => {
      calls.push(message);
      if (message.type === "status") return { ok: true, value: { mode: "reviewed_assistance",
        native_pairing: true, connection: pairedConnection(), assistance: initial } };
      return sendMessage(message);
    },
  }, async (load) => {
    globalThis.chrome.permissions = { contains: async () => true };
    await load();
    const click = async (id) => {
      const control = document.getElementById(id);
      assert.equal(control.disabled, false, id + " must be enabled for the explicit user click");
      return control.listeners.get("click")();
    };
    await run(document, calls, click);
  });
}
function assertAssistanceUnavailable(document) {
  assert.match(document.getElementById("assistance-state").textContent, /Application status is unavailable/);
  assert.match(document.getElementById("assistance-state").textContent, /earlier action may still be active/);
  assert.doesNotMatch(document.getElementById("assistance-state").textContent, /Working/);
  assert.ok(document.getElementById("assistance").querySelectorAll().every((item) => item.disabled));
  assert.equal(document.getElementById("native-pause").disabled, false);
  assert.equal(document.getElementById("native-refresh").disabled, false);
}

test("actual popup ends optimistic Working and stays locked when action and status both fail", async () => {
  const response = deferred();
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_prepare") return response.promise;
    if (type === "assistance_status") return { ok: false, error: "Synthetic current-status denial" };
    assert.fail("No automatic action or retry: " + type);
  }, async (document, calls, click) => {
    document.getElementById("assistance-application").value = "apply_synthetic";
    const pending = click("assistance-prepare");
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(document.getElementById("assistance-state").textContent, /Working/);
    response.resolve({ ok: false, error: currentConnectionError });
    await pending;
    assertAssistanceUnavailable(document);
    assert.equal(document.getElementById("error").textContent, currentConnectionError);
    assert.deepEqual(calls, [{ type: "status" },
      { type: "assistance_prepare", application_id: "apply_synthetic" }, { type: "assistance_status" }]);
  });
});

test("only fresh authoritative status after explicit connection check restores normal controls", async () => {
  let checked = false;
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_prepare") return { ok: false, error: currentConnectionError };
    if (type === "pairing_refresh") { checked = true; return { ok: true, value: pairedConnection() }; }
    if (type === "assistance_status") return checked
      ? { ok: true, value: activeAssistance() } : { ok: false, error: currentConnectionError };
    assert.fail("No automatic action or retry: " + type);
  }, async (document, calls, click) => {
    await click("assistance-prepare");
    assertAssistanceUnavailable(document);
    await click("native-refresh");
    assert.equal(document.getElementById("assistance-prepare").disabled, false);
    assert.equal(document.getElementById("assistance-application").disabled, false);
    assert.equal(document.getElementById("assistance-fill").disabled, true);
    assert.match(document.getElementById("assistance-state").textContent, /Current state: active/);
    assert.deepEqual(calls.map(({ type }) => type), ["status", "assistance_prepare", "assistance_status", "pairing_refresh", "assistance_status"]);
  });
});

test("possible-send failure is never replayed or unlocked from stale approval when status is unavailable", async () => {
  const approved = { ...activeAssistance(), phase: "approved", review_id: "synthetic-retained-review",
    can_fill: true, review_protocol_version: 4 };
  let checked = false;
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_fill") return { ok: false, error: "Synthetic possible-send outcome unavailable" };
    if (type === "pairing_refresh") { checked = true; return { ok: true, value: pairedConnection() }; }
    if (type === "assistance_status") return checked
      ? { ok: true, value: { ...approved, phase: "unknown", can_fill: false, can_reconcile: true,
        message: "The previous outcome is unverified. Automatic replay is blocked." } }
      : { ok: false, error: currentConnectionError };
    assert.fail("No automatic inspection, fill or retry: " + type);
  }, async (document, calls, click) => {
    await click("assistance-fill");
    assertAssistanceUnavailable(document);
    assert.equal(document.getElementById("assistance-review").disabled, true);
    await click("native-refresh");
    assert.equal(document.getElementById("assistance-fill").disabled, true);
    assert.equal(document.getElementById("assistance-prepare").disabled, true);
    assert.equal(document.getElementById("assistance-reconcile").disabled, false);
    assert.match(document.getElementById("assistance-state").textContent, /unverified/);
    assert.equal(calls.filter(({ type }) => type === "assistance_fill").length, 1);
    assert.equal(calls.filter(({ type }) => type.startsWith("assistance_prepare")).length, 0);
  }, approved);
});

test("an authoritative still-busy status stays busy and never invents an idle completion", async () => {
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_prepare") return { ok: false, error: "Synthetic interrupted response" };
    if (type === "assistance_status") return { ok: true,
      value: { ...activeAssistance(), busy: true, message: "The worker still reports an active operation." } };
    assert.fail("No automatic action or retry: " + type);
  }, async (document, calls, click) => {
    await click("assistance-prepare");
    assert.match(document.getElementById("assistance-state").textContent, /worker still reports an active operation/);
    assert.equal(document.getElementById("assistance-prepare").disabled, true);
    assert.equal(document.getElementById("assistance-fill").disabled, true);
    assert.deepEqual(calls.map(({ type }) => type), ["status", "assistance_prepare", "assistance_status"]);
  });
});


test("an older status reply cannot repaint or unlock after the newer unavailable lock", async () => {
  const operation = deferred(), oldStatus = deferred();
  let reads = 0;
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_prepare") return operation.promise;
    if (type === "pairing_refresh" || type === "pairing_status") return { ok: true, value: pairedConnection() };
    if (type === "assistance_status") return ++reads === 1
      ? oldStatus.promise : { ok: false, error: currentConnectionError };
    assert.fail("No automatic request: " + type);
  }, async (document, calls, click) => {
    const action = click("assistance-prepare");
    await new Promise((resolve) => setImmediate(resolve));
    operation.resolve({ ok: false, error: currentConnectionError });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(reads, 1); // The original action still owned its one reconciliation read.
    await click("native-refresh"); // A newer explicit read now refuses and locks the view.
    assertAssistanceUnavailable(document);
    oldStatus.resolve({ ok: true, value: activeAssistance() });
    await action;
    assertAssistanceUnavailable(document);
    assert.deepEqual(calls.map(({ type }) => type), ["status", "assistance_prepare", "assistance_status", "pairing_refresh", "assistance_status", "pairing_status"]);
  });
});

test("an older action reply cannot replace a newer unavailable status or send again", async () => {
  const operation = deferred();
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_prepare") return operation.promise;
    if (type === "pairing_refresh") return { ok: true, value: pairedConnection() };
    if (type === "assistance_status") return { ok: false, error: currentConnectionError };
    if (type === "pairing_status") return { ok: true, value: pairedConnection() };
    assert.fail("No automatic request: " + type);
  }, async (document, calls, click) => {
    const action = click("assistance-prepare");
    await new Promise((resolve) => setImmediate(resolve));
    await click("native-refresh");
    assertAssistanceUnavailable(document);
    operation.resolve({ ok: true, value: activeAssistance() });
    await action;
    assertAssistanceUnavailable(document);
    assert.equal(calls.filter(({ type }) => type === "assistance_prepare").length, 1);
    assert.equal(calls.filter(({ type }) => type === "assistance_fill").length, 0);
  });
});

for (const stage of ["origins", "storage"]) test(`an old render awaiting ${stage} permission cannot enable controls after a newer status refusal`, async () => {
  const permission = deferred();
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_check") return { ok: true, value: { ...activeAssistance(),
      storage_origin: stage === "storage" ? "https://synthetic-bucket.s3.amazonaws.com" : null } };
    if (type === "pairing_refresh" || type === "pairing_status") return { ok: true, value: pairedConnection() };
    if (type === "assistance_status") return { ok: false, error: currentConnectionError };
    assert.fail("No automatic request: " + type);
  }, async (document, calls, click) => {
    globalThis.chrome.permissions.contains = async ({ origins }) =>
      (stage === "origins" ? origins.length === 2 : origins[0].includes("synthetic-bucket.s3.amazonaws.com")) ? permission.promise : true;
    const check = click("assistance-check");
    await new Promise((resolve) => setImmediate(resolve));
    await click("native-refresh");
    assertAssistanceUnavailable(document);
    permission.resolve(true);
    await check;
    assertAssistanceUnavailable(document);
    assert.equal(calls.filter(({ type }) => type === "assistance_check").length, 1);
    assert.equal(calls.filter(({ type }) => type.startsWith("assistance_prepare") || type === "assistance_fill").length, 0);
  });
});


test("a pending optimistic permission render sends no inspection after a newer status lock", async () => {
  const permission = deferred();
  await assistancePopup(async ({ type }) => {
    if (type === "pairing_refresh" || type === "pairing_status") return { ok: true, value: pairedConnection() };
    if (type === "assistance_status") return { ok: false, error: currentConnectionError };
    assert.fail("No inspection, fill or replay after view ownership changed: " + type);
  }, async (document, calls, click) => {
    globalThis.chrome.permissions.contains = async ({ origins }) => origins.length === 2 ? permission.promise : true;
    const action = click("assistance-prepare");
    await new Promise((resolve) => setImmediate(resolve));
    await click("native-refresh");
    assertAssistanceUnavailable(document);
    permission.resolve(true);
    await action;
    assertAssistanceUnavailable(document);
    assert.equal(calls.filter(({ type }) => type.startsWith("assistance_prepare") || type === "assistance_fill").length, 0);
  });
});

test("a native-upload permission grant cannot send an action after its popup view was invalidated", async () => {
  const permission = deferred();
  await assistancePopup(async ({ type }) => {
    if (type === "pairing_refresh" || type === "pairing_status") return { ok: true, value: pairedConnection() };
    if (type === "assistance_status") return { ok: false, error: currentConnectionError };
    assert.fail("No native upload, inspection or fill after view ownership changed: " + type);
  }, async (document, calls, click) => {
    globalThis.chrome.permissions.request = async () => permission.promise;
    const action = click("assistance-prepare-native-upload");
    await new Promise((resolve) => setImmediate(resolve));
    await click("native-refresh");
    assertAssistanceUnavailable(document);
    permission.resolve(true);
    await action;
    assertAssistanceUnavailable(document);
    assert.equal(calls.filter(({ type }) => type.startsWith("assistance_prepare") || type === "assistance_fill").length, 0);
  }, { ...activeAssistance(), native_upload_available: true });
});


for (const newer of ["unavailable", "current"]) test(`an older action failure cannot start reconciliation over a newer ${newer} view`, async () => {
  const operation = deferred();
  let statusReads = 0;
  const authoritative = { ...activeAssistance(), phase: "unknown", review_id: "synthetic-retained-review",
    can_fill: false, can_reconcile: true, message: "Fresh authoritative unknown outcome." };
  await assistancePopup(async ({ type }) => {
    if (type === "assistance_prepare") return operation.promise;
    if (type === "pairing_refresh" || type === "pairing_status") return { ok: true, value: pairedConnection() };
    if (type === "assistance_status") {
      statusReads++;
      if (statusReads > 1) return { ok: true, value: activeAssistance() };
      return newer === "unavailable" ? { ok: false, error: currentConnectionError }
        : { ok: true, value: authoritative };
    }
    assert.fail("No automatic action or replay: " + type);
  }, async (document, calls, click) => {
    const action = click("assistance-prepare");
    await new Promise((resolve) => setImmediate(resolve));
    await click("native-refresh");
    if (newer === "unavailable") assertAssistanceUnavailable(document);
    else assert.equal(document.getElementById("assistance-state").textContent, authoritative.message);
    operation.resolve({ ok: false, error: "Older synthetic action response failed" });
    await action;
    assert.equal(statusReads, 1, "An older failure cannot allocate a new status-read owner");
    if (newer === "unavailable") assertAssistanceUnavailable(document);
    else {
      assert.equal(document.getElementById("assistance-state").textContent, authoritative.message);
      assert.equal(document.getElementById("assistance-prepare").disabled, true);
      assert.equal(document.getElementById("assistance-fill").disabled, true);
    }
    assert.equal(document.getElementById("error").textContent, "Older synthetic action response failed");
    assert.equal(calls.filter(({ type }) => type === "assistance_prepare").length, 1);
    assert.equal(calls.filter(({ type }) => type === "assistance_fill").length, 0);
  });
});
