// Actual Chrome runtime/key/socket probes. This server deliberately returns an
// unavailable response; it is not the protected native first-connection proof.
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";
import { createServer } from "node:https";
import { createHash, X509Certificate, generateKeyPairSync } from "node:crypto";
import { chromium, expect } from "@playwright/test";
import { nativeFixtureBuild } from "../scripts/native-fixture-build.js";
import { canonical } from "../extension/protocol.js";
import { readinessFixture } from "./pairing-readiness-fixture.js";

async function runtimeFixture({ hold = false, grantTestOrigin = false, coldKey = false, inspectedKeyStatus = null,
  executorRevision = "owned-runtime-socket-probe-v1" } = {}) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-mv3-runtime-browser-`);
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", `${directory}/tls.key`, "-out", `${directory}/tls.crt`, "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"], { stdio: "ignore" });
  const certificate = await readFile(`${directory}/tls.crt`);
  const spki = new X509Certificate(certificate).publicKey.export({ type: "spki", format: "der" });
  const pin = createHash("sha256").update(spki).digest("base64");
  const requests = [], readinessRequests = [], pending = new Set();
  let extensionId;
  const server = createServer({ key: await readFile(`${directory}/tls.key`), cert: certificate }, (req, res) => {
    const ready = readinessFixture({ url: `https://127.0.0.1${req.url}`, method: req.method, headers: req.headers, body: null }, "https://127.0.0.1", extensionId);
    if (ready) { readinessRequests.push(req.url); req.resume(); res.writeHead(ready.status, ready.headers); res.end(); return; }
    // Record endpoint names only: no bodies, headers, candidate or key material.
    requests.push(req.url);
    if (req.url === "/api/browser-pairing/device/inspect-key" && inspectedKeyStatus) {
      const chunks = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        const proof = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        const key_sha256 = createHash("sha256").update(`hirewiz.pairing.public-key.v2\n${canonical(proof.key)}`).digest("hex");
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ protocol_version: 2, key_sha256, extension_id: proof.extension_id,
          executor_revision: proof.revision, status: inspectedKeyStatus }));
      });
      return;
    }
    req.resume();
    if (hold) { pending.add(res); res.on("close", () => pending.delete(res)); return; }
    res.writeHead(503, { "Content-Type": "application/json" }); res.end('{"error":"unavailable"}');
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `https://127.0.0.1:${server.address().port}`;
  const { publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const authorityKey = publicKey.export({ format: "jwk" });
  const build = await nativeFixtureBuild(`${directory}/extension`, { websiteOrigin: origin, authorityKey, executorRevision, grantTestOrigin });
  extensionId = build.extensionId;
  let context;
  const launch = async (unavailable = false) => {
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: "chromium", headless: true,
      args: [`--disable-extensions-except=${directory}/extension`, `--load-extension=${directory}/extension`, `--ignore-certificate-errors-spki-list=${pin}`] });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    assert.equal(new URL(worker.url()).host, build.extensionId);
    const popup = await context.newPage(); await popup.goto(`chrome-extension://${build.extensionId}/popup.html${coldKey ? "?cold-key-probe=true" : ""}`);
    await expect(popup.locator("#mode")).toContainText(coldKey || unavailable ? "Browser connection unavailable" : "Controlled local native connection test");
    coldKey = false;
    return { worker, popup };
  };
  try {
    const current = await launch();
    return { ...current, origin, requests, readinessRequests, directory, get context() { return context; },
      async restart({ unavailable = false } = {}) { await context.close(); return launch(unavailable); },
      async close() { await context?.close(); for (const response of pending) response.destroy(); server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); await rm(directory, { recursive: true, force: true }); } };
  } catch (error) { await context?.close(); for (const response of pending) response.destroy(); server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); await rm(directory, { recursive: true, force: true }); throw error; }
}

const checkpoint = (worker) => worker.evaluate(async () => (await chrome.storage.local.get("pairing_checkpoint")).pairing_checkpoint);

test("actual MV3: concurrent cold device reads preserve one nonextractable signing owner, across browser restart", async () => {
  const f = await runtimeFixture({ coldKey: true }); try {
    const first = await f.popup.evaluate(async () => {
      const { deviceIdentity } = await import(chrome.runtime.getURL("device.js"));
      const identities = await Promise.all(Array.from({ length: 8 }, () => deviceIdentity()));
      return Promise.all(identities.map(async (identity) => {
        let exported = false; try { await crypto.subtle.exportKey("jwk", identity.private_key); exported = true; } catch { /* nonextractable */ }
        return { device_id: identity.device_id, key_sha256: identity.key_sha256, extractable: identity.private_key.extractable, exported };
      }));
    });
    assert.equal(new Set(first.map((item) => item.device_id)).size, 1);
    assert.equal(new Set(first.map((item) => item.key_sha256)).size, 1);
    assert.ok(first.every((item) => item.extractable === false && item.exported === false));
    const next = await f.restart();
    const retained = await next.popup.evaluate(async () => { const i = await (await import(chrome.runtime.getURL("device.js"))).deviceIdentity(); return { device_id: i.device_id, key_sha256: i.key_sha256 }; });
    assert.equal(retained.device_id, first[0].device_id); assert.equal(retained.key_sha256, first[0].key_sha256);
    assert.deepEqual(f.requests, []);
  } finally { await f.close(); }
});

test("actual MV3: only exact popup messages are admitted, application operations and client context are denied", async () => {
  const f = await runtimeFixture(); try {
    for (const message of [{ type: "fill" }, { type: "prepare" }, { type: "enroll" }, { type: "pairing_start", session: "forbidden" }, { type: "pairing_start", password: "forbidden" }]) {
      const reply = await f.popup.evaluate((value) => chrome.runtime.sendMessage(value), message);
      assert.equal(reply.ok, false);
    }
    const other = await f.context.newPage(); await other.goto(`${f.popup.url()}?untrusted=true`);
    const reply = await other.evaluate(async () => { try { return await chrome.runtime.sendMessage({ type: "pairing_start" }); } catch { return null; } });
    assert.equal(reply == null, true);
    assert.deepEqual(f.requests, []);
    const permissions = await f.popup.evaluate(() => chrome.permissions.getAll()); assert.deepEqual(permissions.origins, []);
    assert.deepEqual(permissions.permissions, ["storage"]);
    await expect(f.popup.locator("#fixture-connection")).toBeHidden(); await expect(f.popup.locator("#fixture-prepare")).toBeHidden();
  } finally { await f.close(); }
});

test("actual MV3: unavailable socket outcome persists unknown; restart and cancel cannot enable automatic retry", async () => {
  // Headless Chrome cannot approve its native permissions dialog. This explicit
  // test-only manifest grant is not evidence for that human interaction.
  const f = await runtimeFixture({ grantTestOrigin: true }); try {
    await expect(f.popup.locator("#native-start")).toBeEnabled();
    assert.deepEqual((await f.popup.evaluate(() => chrome.permissions.getAll())).origins, [`${f.origin}/*`]);
    await f.popup.getByRole("button", { name: "Start browser connection" }).click();
    await expect(f.popup.getByRole("alert")).toContainText("outcome needs inspection");
    const state = await checkpoint(f.worker); assert.equal(state.phase, "unknown");
    assert.deepEqual(f.requests, ["/api/browser-pairing/device/prepare"]);
    assert.deepEqual(f.readinessRequests, ["/api/browser-pairing/device/prepare"]);
    assert.equal(JSON.stringify(state).includes("password"), false); assert.equal(JSON.stringify(state).includes("claim"), false);
    const next = await f.restart();
    await expect(next.popup.locator("#native-state")).toContainText("Automatic retry");
    await expect(next.popup.locator("#native-start")).toBeDisabled();
    await next.popup.getByRole("button", { name: "Pause connection requests" }).click();
    const reply = await next.popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_start" })); assert.equal(reply.ok, false);
    assert.deepEqual(f.requests, ["/api/browser-pairing/device/prepare"]);
  } finally { await f.close(); }
});

test("actual MV3: Pause during a held HTTPS response aborts further work and retains unknown", async () => {
  const f = await runtimeFixture({ hold: true, grantTestOrigin: true }); try {
    await expect(f.popup.locator("#native-start")).toBeEnabled(); await f.popup.getByRole("button", { name: "Start browser connection" }).click();
    await expect.poll(() => f.requests.length).toBe(1);
    await expect(f.popup.locator("#native-pause")).toBeEnabled(); await f.popup.locator("#native-pause").click();
    await expect.poll(async () => (await checkpoint(f.worker)).phase).toBe("unknown");
    await expect(f.popup.locator("#native-start")).toBeDisabled(); assert.equal(f.requests.length, 1);
  } finally { await f.close(); }
});

test("actual MV3: missing exact website grant blocks the next request without disclosing any body", async () => {
  const f = await runtimeFixture(); try {
    await expect(f.popup.locator("#native-start")).toBeDisabled();
    const reply = await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_start" }));
    assert.equal(reply.ok, false); assert.deepEqual(f.requests, []);
    const status = await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_status" }));
    assert.equal(status.value.phase, "idle"); assert.deepEqual(status.value.allowed_actions, []);
  } finally { await f.close(); }
});

test("actual MV3: corrupted retained key is refused without replacing it", async () => {
  const f = await runtimeFixture(); try {
    const result = await f.popup.evaluate(async () => {
      const { deviceIdentity } = await import(chrome.runtime.getURL("device.js")); const original = await deviceIdentity();
      await new Promise((resolve, reject) => { const open = indexedDB.open("hirewiz-companion-device", 1); open.onerror = reject;
        open.onsuccess = () => { const db = open.result, tx = db.transaction("keys", "readwrite"), store = tx.objectStore("keys"), read = store.get("device");
          read.onsuccess = () => store.put({ ...read.result, key_sha256: "0".repeat(64) }, "device"); tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = reject; }; });
      let rejected = false; try { await deviceIdentity(); } catch (error) { rejected = error.message === "Device key storage is unavailable"; }
      const retained = await new Promise((resolve, reject) => { const open = indexedDB.open("hirewiz-companion-device", 1); open.onerror = reject;
        open.onsuccess = () => { const db = open.result, read = db.transaction("keys", "readonly").objectStore("keys").get("device");
          read.onsuccess = () => { db.close(); resolve({ device_id: read.result.device_id, key_sha256: read.result.key_sha256 }); }; read.onerror = reject; }; });
      return { rejected, same_owner: retained.device_id === original.device_id, corrupted_retained: retained.key_sha256 === "0".repeat(64) };
    });
    assert.deepEqual(result, { rejected: true, same_owner: true, corrupted_retained: true }); assert.deepEqual(f.requests, []);
  } finally { await f.close(); }
});

for (const inspectedKeyStatus of ["UNCLAIMED", "CLAIMED"]) {
  test(`actual MV3: ID-less ${inspectedKeyStatus} inspection permits only its explicit recovery action`, async () => {
    const f = await runtimeFixture({ grantTestOrigin: true, inspectedKeyStatus }); try {
      await f.popup.getByRole("button", { name: "Start browser connection" }).click();
      await expect(f.popup.getByRole("alert")).toContainText("outcome needs inspection");
      await expect(f.popup.locator("#native-inspect")).toBeEnabled();
      await f.popup.locator("#native-inspect").click();
      await expect(f.popup.locator("#native-state")).toContainText(inspectedKeyStatus === "UNCLAIMED" ? "key is unclaimed" : "stay blocked");
      await expect(f.popup.locator("#native-start")).toBeDisabled(); await expect(f.popup.locator("#native-resume-existing")).toBeDisabled();
      assert.deepEqual(f.requests, ["/api/browser-pairing/device/prepare", "/api/browser-pairing/device/inspect-key"]);
      if (inspectedKeyStatus === "UNCLAIMED") {
        await expect(f.popup.locator("#native-archive")).toBeEnabled(); await f.popup.locator("#native-archive").click();
        await expect(f.popup.locator("#native-start")).toBeEnabled();
        const state = await checkpoint(f.worker); assert.equal(state.phase, "idle"); assert.equal(state.history.length, 1);
        assert.equal(state.history[0].phase, "unknown"); assert.equal(state.history[0].send_kind, "prepare");
      } else await expect(f.popup.locator("#native-archive")).toBeDisabled();
      assert.equal(f.requests.length, 2); // No hidden fresh start or create replay.
    } finally { await f.close(); }
  });
}

test("actual MV3: legacy checkpoint with missing retained IDB key is refused without creating a replacement", async () => {
  const f = await runtimeFixture({ executorRevision: "companion_0_8_1" }); try {
    const legacy = await f.popup.evaluate(async () => {
      const { CONFIG } = await import(chrome.runtime.getURL("config.js"));
      const { canonical, digest } = await import(chrome.runtime.getURL("protocol.js"));
      const value = { version: 1, phase: "unknown", binding: await digest(canonical({ website_origin: CONFIG.websiteOrigin,
        extension_id: CONFIG.extensionId, executor_revision: "companion_0_8_0", authority_key: CONFIG.authorityKey })),
      pairing_id: null, device_id: null, key_sha256: null, request_sha256: null, expires_at_ms: null, review_url: null,
      local_operation_id: crypto.randomUUID(), native_operation_id: null, updated_at_ms: Date.now(), resume_phase: null };
      await chrome.storage.local.set({ pairing_checkpoint: value });
      await new Promise((resolve, reject) => {
        const request = indexedDB.open("hirewiz-companion-device", 1); request.onerror = reject;
        request.onsuccess = () => { const db = request.result, tx = db.transaction("keys", "readwrite");
          tx.objectStore("keys").delete("device"); tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = reject; };
      });
      return value;
    });
    const next = await f.restart({ unavailable: true });
    const missing = await next.popup.evaluate(async () => new Promise((resolve, reject) => {
      const request = indexedDB.open("hirewiz-companion-device", 1); request.onerror = reject;
      request.onsuccess = () => { const db = request.result, read = db.transaction("keys", "readonly").objectStore("keys").get("device");
        read.onsuccess = () => { const absent = read.result === undefined; db.close(); resolve(absent); }; read.onerror = reject; };
    }));
    assert.equal(missing, true); assert.deepEqual(await checkpoint(next.worker), legacy); assert.deepEqual(f.requests, []);
    await expect(next.popup.locator("#native-start")).toBeDisabled();
  } finally { await f.close(); }
});
