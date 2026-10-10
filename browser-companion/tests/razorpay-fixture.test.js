import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { ASSISTED_FIELDS, CHOICES, RECIPIENT_POLICY, validateAnswers } from "../fixtures/razorpay/schema.js";
import { fixtureHtml } from "../fixtures/razorpay/form.js";
import { startRazorpayFixture } from "../fixtures/razorpay/server.js";

test("synthetic schema preserves seven assisted fields and manual sensitive choices", () => {
  assert.equal(ASSISTED_FIELDS.length, 7);
  assert.equal(new Set(ASSISTED_FIELDS.map(([id]) => id)).size, 7);
  assert.equal(CHOICES.question_8970459005.length, 27);
  assert.deepEqual(CHOICES.question_8970458005.map(({ label }) => label), ["Male", "Female", "Others"]);
  assert.equal(ASSISTED_FIELDS.some(([id]) => ["phone", "resume", "country", "question_8970458005"].includes(id)), false);
  assert.match(RECIPIENT_POLICY.limitations, /not verification/);
});

test("answers reject empty, overlong, invented or sensitive manual values", () => {
  assert.deepEqual(validateAnswers({ first_name: "Synthetic Alice" }), ["first_name"]);
  for (const values of [null, [], {}, { first_name: "" }, { first_name: " ", last_name: "Fixture" }, { first_name: true }, { first_name: "a".repeat(256) }, { question_8970458005: "Male" }, { phone: "123" }, { resume: "bytes" }, { invented: "value" }]) assert.throws(() => validateAnswers(values));
});

test("hand-authored page has no external scripts, assets or actions and no inferred consent", () => {
  const html = fixtureHtml();
  assert.doesNotMatch(html, /(?:src|href|action)=["']https?:/);
  assert.doesNotMatch(html, /<input[^>]*checked|__remixContext|grecaptcha|job-boards\.cdn/);
  assert.match(html, /type="tel"/); assert.match(html, /role="combobox"/); assert.match(html, /id="company-name-0"/);
  assert.match(html, /No employer, candidate account or production authority/);
});

test("loopback recorder distinguishes email, manual file and manual submit without receipt", async () => {
  const server = await startRazorpayFixture();
  try {
    assert.match(server.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal((await fetch(`${server.origin}/fixture/razorpay-like/apply`)).status, 200);
    await fetch(`${server.origin}/fixture/razorpay-like/email-validator?address=synthetic%40example.test`);
    const file = await fetch(`${server.origin}/fixture/razorpay-like/manual-file`, { method: "POST", body: "synthetic-file" });
    assert.equal((await file.json()).employer_receipt, null);
    await fetch(`${server.origin}/fixture/razorpay-like/manual-submit`, { method: "POST", body: "synthetic-only" });
    assert.deepEqual(server.events.map(({ kind }) => kind), ["email_validation", "manual_file", "manual_submit"]);
    assert.equal((await fetch(`${server.origin}/fixture/device-action`, { method: "POST" })).status, 404);
    assert.equal((await fetch(`${server.origin}/fixture/razorpay-like/manual-file`, { method: "POST", body: "a".repeat(100_001) })).status, 413);
    assert.equal(server.events.length, 3);
  } finally { await server.close(); }
});

test("shipping manifest and configuration remain disabled; scaffold has no authority wiring", async () => {
  const manifest = JSON.parse(await readFile(new URL("../extension/manifest.json", import.meta.url)));
  assert.deepEqual(manifest.host_permissions, []); assert.deepEqual(manifest.optional_host_permissions, []);
  assert.equal(manifest.content_scripts, undefined);
  assert.match(await readFile(new URL("../extension/config.js", import.meta.url), "utf8"), /mode: "disabled"/);
  const server = await readFile(new URL("../fixtures/razorpay/server.js", import.meta.url), "utf8");
  assert.doesNotMatch(server, /fixtureAuthority|fetch\(|device-action|recover|enroll/);
});
