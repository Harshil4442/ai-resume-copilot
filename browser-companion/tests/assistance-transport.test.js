import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";
import { AssistanceTransport } from "../extension/assistance-transport.js";
import { base64url, canonical } from "../extension/protocol.js";
import { fakeRequestDeadlines } from "./request-deadlines.js";

const now = 1800000000000;
const deviceId = "11111111-1111-4111-8111-111111111111";
const configuration = { mode: "reviewed_assistance", websiteOrigin: "https://www.hirewizhq.com",
  portalOrigin: "https://job-boards.greenhouse.io", extensionId: "pnjmegppacleipajnifjmbcopjjffklb" };

async function setup() {
  const device = await webcrypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  let clock = now;
  const transport = new AssistanceTransport({ configuration,
    identity: { device_id: deviceId, public_key: await webcrypto.subtle.exportKey("jwk", device.publicKey), private_key: device.privateKey },
    beforePost: async () => undefined, now: () => clock });
  return { transport, advance: (milliseconds) => { clock += milliseconds; } };
}

test("assistance accepts an abort-aware 13.025-second response with one unchanged signed request", async (t) => {
  const f = await setup(), deadlines = fakeRequestDeadlines(t);
  let calls = 0, started, signal, submitted, outcome = "pending";
  const dispatched = new Promise((resolve) => { started = resolve; });
  t.mock.method(globalThis, "fetch", (_url, options) => new Promise((resolve, reject) => {
    calls++; signal = options.signal; submitted = JSON.parse(options.body);
    assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error");
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    setTimeout(() => resolve(Response.json({ state: "active" })), 13025);
    started();
  }));
  const result = f.transport.request("status", {}).then((value) => { outcome = "resolved"; return value; },
    (error) => { outcome = "rejected"; throw error; });
  result.catch(() => undefined);
  await dispatched;
  f.advance(12000); t.mock.timers.tick(12000); await Promise.resolve();
  assert.equal(outcome, "pending"); assert.equal(signal.aborted, false);
  f.advance(1025); t.mock.timers.tick(1025);
  assert.deepEqual(await result, { state: "active" });
  assert.deepEqual(deadlines, [40000]);
  assert.equal(submitted.issued_at_ms, now);
  assert.equal(submitted.protocol_version, 1);
  assert.equal(calls, 1);
});

test("assistance aborts at forty seconds without retrying the uncertain request", async (t) => {
  const f = await setup(), deadlines = fakeRequestDeadlines(t);
  let calls = 0, started, signal;
  const dispatched = new Promise((resolve) => { started = resolve; });
  t.mock.method(globalThis, "fetch", (_url, options) => new Promise((_resolve, reject) => {
    calls++; signal = options.signal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    started();
  }));
  const failed = assert.rejects(f.transport.request("inspect", { application_id: "apply_synthetic" }), { name: "TimeoutError" });
  await dispatched;
  t.mock.timers.tick(39999); assert.equal(signal.aborted, false);
  t.mock.timers.tick(1); await failed;
  assert.equal(signal.aborted, true);
  t.mock.timers.tick(100000);
  assert.deepEqual(deadlines, [40000]); assert.equal(calls, 1);
});

test("longer assistance transport waiting never extends the ten-second signed action lease", async (t) => {
  const f = await setup();
  const authority = await webcrypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  f.transport.authorityKey = await webcrypto.subtle.exportKey("jwk", authority.publicKey);
  const payload = { protocol_version: 1, device_id: deviceId, action_id: "22222222-2222-4222-8222-222222222222",
    issued_at_ms: now, deadline: now + 10000, allowed_actions: ["fill"] };
  const signature = base64url(await webcrypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, authority.privateKey,
    new TextEncoder().encode(`hirewiz.browser-assistance.lease.v1\n${canonical(payload)}`)));
  const envelope = { payload, signature };
  assert.deepEqual(await f.transport.verifyLease(envelope), payload);
  fakeRequestDeadlines(t);
  let calls = 0, started;
  const dispatched = new Promise((resolve) => { started = resolve; });
  t.mock.method(globalThis, "fetch", (_url, options) => new Promise((resolve, reject) => {
    calls++; options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
    setTimeout(() => resolve(Response.json({ state: "begun", lease: envelope })), 13025);
    started();
  }));
  const result = f.transport.request("begin", { review_id: "33333333-3333-4333-8333-333333333333" });
  await dispatched; f.advance(13025); t.mock.timers.tick(13025);
  const returned = await result;
  await assert.rejects(f.transport.verifyLease(returned.lease), /action expired or changed/);
  assert.equal(calls, 1);
});
