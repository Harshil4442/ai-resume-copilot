// Installed MV3 → signed device transport → actual isolated SQL/crypto service
// → actual Chrome document adapter. Every HTTPS request is locally fulfilled;
// the candidate approval calls use owned synthetic user 1, not provider access.
import assert from "node:assert/strict";
import test from "node:test";
import { cp, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { chromium, expect } from "@playwright/test";
import { fixtureHtml } from "../fixtures/razorpay/form.js";
import { typedFixtureHtml } from "../fixtures/razorpay/typed-form.js";

const ROOT = new URL("../../", import.meta.url);
const WEBSITE = "https://www.hirewizhq.com";
const PORTAL = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005";
const EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb";
const message = (popup, value) => popup.evaluate((input) => chrome.runtime.sendMessage(input), value);
async function fixture({ granted = true, loseBegin = false, typed = false, unknownOption = false, conditional = false, tamperChoiceLease = false } = {}) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-reviewed-assistance-`);
  const pending = []; const requests = []; const leases = []; let errors = ""; let context;
  const python = spawn(new URL("backend/.venv/bin/python", ROOT).pathname,
    ["-I", new URL("backend/tests/browser_assistance_chrome_bridge.py", ROOT).pathname],
    { cwd: new URL("backend/", ROOT).pathname, stdio: ["pipe", "pipe", "pipe"] });
  python.stderr.on("data", (chunk) => { errors += chunk.toString(); });
  createInterface({ input: python.stdout }).on("line", (line) => pending.shift()?.resolve(JSON.parse(line)));
  python.on("exit", () => { for (const waiter of pending.splice(0)) waiter.reject(new Error("Owned SQL bridge exited")); });
  const bridge = (value) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Owned SQL bridge timed out")), 10000);
    pending.push({ resolve: (reply) => { clearTimeout(timer); resolve(reply); }, reject: (failure) => { clearTimeout(timer); reject(failure); } });
    python.stdin.write(`${JSON.stringify(value)}\n`);
  });
  try {
    const typedApplication = typed ? await bridge({ operation: "configure_typed" }) : null;
    if (typed) assert.equal(typedApplication.status, 200);
    const extension = `${directory}/extension`;
    await cp(new URL("../extension/", import.meta.url), extension, { recursive: true });
    const manifest = JSON.parse(await readFile(`${extension}/manifest.json`, "utf8"));
    // A narrow test-only host grant avoids automating Chrome's human permissions
    // dialog. Production still declares optional grants with no granted hosts.
    if (granted) manifest.host_permissions = manifest.optional_host_permissions;
    await writeFile(`${extension}/manifest.json`, JSON.stringify(manifest));
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: "chromium", headless: true,
      serviceWorkers: "allow", args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    assert.equal(new URL(worker.url()).host, EXTENSION);
    let dropped = false;
    await context.route("**/*", async (route) => {
      const request = route.request(), url = request.url();
      if (url.startsWith("chrome-extension:")) return route.continue();
      requests.push({ url, method: request.method(), origin: request.headers().origin ?? null });
      if (url === PORTAL && request.method() === "GET") {
        const html = typed ? typedFixtureHtml(typedApplication.body.field_mapping, PORTAL, { unknownOption, conditional })
          : fixtureHtml().replace(/<main[^>]*>/, "<main>").replace(/<script type="module">[\s\S]*?<\/script>/,
          `<script>window.events={input:[],change:[],blur:[],submit:0};document.querySelector('form').addEventListener('input',e=>events.input.push(e.target.id));document.querySelector('form').addEventListener('change',e=>events.change.push(e.target.id));document.querySelector('#email').addEventListener('blur',()=>events.blur.push('email'));document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();events.submit++});</script>`)
          .replace('action="/fixture/razorpay-like/apply"', `action="${PORTAL}"`);
        return route.fulfill({ status: 200, contentType: "text/html", body: html });
      }
      if (url.startsWith(`${WEBSITE}/api/browser-assistance/device/`) && request.method() === "POST") {
        const origin = request.headers().origin;
        const result = await bridge({ operation: "device", origin: origin || "", body: request.postDataJSON() });
        if (url.endsWith("/begin") && result.status === 200 && result.body.lease) {
          leases.push(structuredClone(result.body.lease));
          if (tamperChoiceLease && result.body.lease.payload.control_type === "combobox") {
            // Alter the actual signed label in transport, keeping its signature.
            result.body.lease.payload.option_label = "Unapproved transport choice";
          }
        }
        if (loseBegin && !dropped && url.endsWith("/begin") && result.status === 200) { dropped = true; return route.abort("failed"); }
        return route.fulfill({ status: result.status, contentType: "application/json", body: JSON.stringify(result.body) });
      }
      return route.abort("blockedbyclient");
    });
    const popup = await context.newPage(); await popup.goto(`chrome-extension://${EXTENSION}/popup.html`);
    await expect(popup.locator("#mode")).toContainText("Candidate-reviewed Razorpay assistance");
    const portal = await context.newPage(); await portal.goto(PORTAL);
    const connect = async () => {
      await popup.locator("#assistance-connect").click();
      await expect(popup.locator("#assistance-state")).toContainText("Review this browser fingerprint");
      const result = await message(popup, { type: "status" }); assert.equal(result.ok, true, result.error);
      const candidate = await bridge({ operation: "candidate", action: "confirm", body: { identity: result.value.device_id, confirmed: true, key_sha256: result.value.key_sha256 } });
      assert.equal(candidate.status, 200);
      await popup.locator("#assistance-check").click();
      await expect(popup.locator("#assistance-prepare")).toBeEnabled();
      const checked = await message(popup, { type: "status" }); assert.equal(checked.ok, true, checked.error); assert.equal(checked.value.phase, "active");
    };
    const prepare = async () => {
      await popup.locator("#assistance-application").fill("apply_test");
      await popup.locator("#assistance-prepare").click();
      await expect(popup.locator("#assistance-review")).toBeEnabled();
      const result = await message(popup, { type: "status" }); assert.equal(result.ok, true, result.error);
      const reviewed = await bridge({ operation: "candidate", action: "review", body: { identity: result.value.review_id } });
      assert.equal(reviewed.status, 200); return reviewed.body;
    };
    const approve = async (review) => {
      const response = await bridge({ operation: "candidate", action: "approve", body: { identity: review.id, confirmed: true, review_digest: review.review_digest } }); assert.equal(response.status, 200);
      await popup.locator("#assistance-check").click(); await expect(popup.locator("#assistance-fill")).toBeEnabled();
      return message(popup, { type: "status" });
    };
    return { popup, portal, worker, context, bridge, requests, leases, typedApplication: typedApplication?.body, connect, prepare, approve,
      async close() {
        await context?.close(); assert.equal(context.pages().length, 0);
        python.stdin.end(); await new Promise((resolve) => python.once("exit", resolve));
        assert.equal(errors, "");
        await rm(directory, { recursive: true, force: true }); await assert.rejects(stat(directory), { code: "ENOENT" });
        assert.ok(requests.every(({ url, method }) => url === PORTAL && method === "GET"
          || url.startsWith(`${WEBSITE}/api/browser-assistance/device/`) && method === "POST"), "All requests are owned local fulfillments; no employer or processor requests");
      } };
  } catch (error) { await context?.close(); python.kill(); await rm(directory, { recursive: true, force: true }); throw error; }
}

test("installed production MV3 proves actual extension Origin and reviewed SQL/crypto fill-only flow", async () => {
  const f = await fixture(); try {
    await f.connect(); const review = await f.prepare();
    const premature = await message(f.popup, { type: "assistance_fill" }); assert.equal(premature.ok, false);
    assert.deepEqual(await f.portal.evaluate(() => events.input), []);
    const approved = await f.approve(review); assert.equal(approved.value.can_fill, true);
    await f.popup.locator("#assistance-fill").click();
    await expect(f.popup.locator("#assistance-state")).toContainText("Approved text filling finished");
    await expect(f.popup.locator("#assistance-check")).toBeEnabled();
    const filled = await message(f.popup, { type: "status" }); assert.equal(filled.ok, true, filled.error); assert.equal(filled.value.phase, "filled"); assert.equal(filled.value.filled_count, 7); assert.equal(filled.value.busy, false);
    const events = await f.portal.evaluate(() => window.events); assert.equal(events.input.length, 7); assert.equal(events.change.length, 7); assert.deepEqual(events.blur, ["email"]); assert.equal(events.submit, 0);
    assert.equal(await f.portal.locator("input[type=file]").evaluate((input) => input.files.length), 0);
    assert.deepEqual((await f.bridge({ operation: "audit" })).body, { charged_credits: 0, application_status: "ready", field_count: 7 });
    const posts = f.requests.filter((entry) => entry.method === "POST"); assert.ok(posts.length >= 19);
    assert.ok(posts.every((entry) => entry.origin === `chrome-extension://${EXTENSION}`));
    const stored = await f.worker.evaluate(async () => (await chrome.storage.local.get("assistance_checkpoint")).assistance_checkpoint);
    assert.equal(Object.hasOwn(stored, "payload"), false); assert.equal(Object.hasOwn(stored, "answers"), false);
  } finally { await f.close(); }
});

test("installed MV3 lost actual begin acknowledgement stays unknown, never replays or fills", async () => {
  const f = await fixture({ loseBegin: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    const first = await message(f.popup, { type: "assistance_fill" }); assert.equal(first.ok, false);
    const state = await message(f.popup, { type: "status" }); assert.equal(state.value.phase, "unknown");
    const retry = await message(f.popup, { type: "assistance_fill" }); assert.equal(retry.ok, false);
    assert.equal(f.requests.filter((entry) => entry.url.endsWith("/begin")).length, 1);
    assert.deepEqual(await f.portal.evaluate(() => events.input), []);
    assert.equal((await f.bridge({ operation: "audit" })).body.field_count, 1);
  } finally { await f.close(); }
});

test("installed MV3 missing optional origin permission blocks all device transport", async () => {
  const f = await fixture({ granted: false }); try {
    const result = await message(f.popup, { type: "assistance_connect" }); assert.equal(result.ok, false);
    assert.equal(f.requests.filter((entry) => entry.method === "POST").length, 0);
    assert.deepEqual(await f.portal.evaluate(() => events.input), []);
  } finally { await f.close(); }
});

test("installed MV3 v2 fills twelve actual SQL-approved signed fields, including phone and four exact choices, for zero fee", async () => {
  const f = await fixture({ typed: true }); try {
    await f.connect(); const review = await f.prepare();
    assert.equal(review.payload.review_protocol_version, 2);
    assert.equal(review.payload.fields.length, 12);
    assert.deepEqual(review.payload.field_mapping, f.typedApplication.field_mapping);
    assert.deepEqual(review.payload.reviewed_answers, f.typedApplication.answers);
    const premature = await message(f.popup, { type: "assistance_fill" }); assert.equal(premature.ok, false);
    assert.equal(f.leases.length, 0);
    await f.approve(review);
    const result = await message(f.popup, { type: "assistance_fill" }); assert.equal(result.ok, true, result.error);
    assert.equal(result.value.phase, "filled"); assert.equal(result.value.filled_count, 12);
    assert.match(result.value.message, /Approved answers filled/);
    assert.equal(f.leases.length, 12);
    for (const [index, envelope] of f.leases.entries()) {
      const field = review.payload.fields[index], lease = envelope.payload;
      assert.equal(lease.review_protocol_version, 2); assert.equal(lease.control_type, field.type);
      assert.equal(lease.field_id, field.id); assert.equal(lease.value, field.value);
      assert.equal(lease.expected_before, field.expected_before); assert.equal(lease.review_digest, review.review_digest);
      assert.equal(lease.document_id, review.payload.document_id); assert.equal(lease.package_digest, review.payload.package_digest);
      assert.equal(lease.artifact_sha256, review.payload.artifact.sha256); assert.equal(lease.target_url, PORTAL);
      assert.deepEqual(lease.allowed_actions, ["fill"]);
      assert.ok(lease.deadline > lease.issued_at_ms && lease.deadline - lease.issued_at_ms <= 10000);
      if (field.type === "combobox") assert.equal(lease.option_label, field.value_label);
      else assert.equal(Object.hasOwn(lease, "option_label"), false);
      assert.equal(envelope.signature.length, 86);
    }
    const events = await f.portal.evaluate(() => window.events);
    const textFields = review.payload.fields.filter(({ type }) => type !== "combobox");
    assert.deepEqual(events.input, textFields.map(({ id }) => id)); assert.deepEqual(events.change, events.input);
    assert.deepEqual(events.blur, ["email"]);
    const choices = review.payload.fields.filter(({ type }) => type === "combobox");
    assert.deepEqual(events.choice, choices.map(({ id, value_label }) => ({ id, label: value_label })));
    assert.deepEqual(events.opens, choices.map(({ id }) => id)); assert.equal(events.submit, 0);
    await expect(f.portal.locator("#phone")).toHaveValue("+919000000000");
    await expect(f.portal.locator("#country")).toHaveValue(""); await expect(f.portal.locator("#company-name-0")).toHaveValue("");
    assert.equal(await f.portal.locator("input[type=file]").evaluate((input) => input.files.length), 0);
    assert.deepEqual((await f.bridge({ operation: "audit", detailed: true })).body, {
      charged_credits: 0, application_status: "ready", field_count: 12, review_state: "filled", next_index: 12, receipt: null, reservation_count: 0,
    });
    assert.ok(f.requests.filter(({ method }) => method === "POST").every(({ origin }) => origin === `chrome-extension://${EXTENSION}`));
    const stored = await f.worker.evaluate(async () => (await chrome.storage.local.get("assistance_checkpoint")).assistance_checkpoint);
    assert.equal(stored.filled_count, 12); assert.equal(Object.hasOwn(stored, "payload"), false); assert.equal(Object.hasOwn(stored, "answers"), false);
  } finally { await f.close(); }
});

for (const scenario of [{ unknownOption: true }, { conditional: true }, { tamperChoiceLease: true }]) {
  test(`installed MV3 v2 stops unknown choice outcome without a later lease or replay: ${Object.keys(scenario)[0]}`, async () => {
    const f = await fixture({ typed: true, ...scenario }); try {
      await f.connect(); const review = await f.prepare(); await f.approve(review);
      const result = await message(f.popup, { type: "assistance_fill" });
      assert.equal(result.ok, !scenario.tamperChoiceLease);
      if (scenario.tamperChoiceLease) assert.match(result.error, /exact action signature could not be verified/);
      if (result.ok) assert.doesNotMatch(result.value.message, /Approved answers filled/);
      const state = await message(f.popup, { type: "status" }); assert.equal(state.value.phase, "unknown"); assert.equal(state.value.filled_count, 8);
      const retry = await message(f.popup, { type: "assistance_fill" }); assert.equal(retry.ok, false);
      assert.equal(f.leases.length, 9); assert.equal(f.requests.filter(({ url }) => url.endsWith("/begin")).length, 9);
      const events = await f.portal.evaluate(() => window.events);
      assert.equal(events.input.length, 8); assert.equal(events.submit, 0);
      assert.equal(events.opens.length, scenario.tamperChoiceLease ? 0 : 1);
      assert.equal(events.choice.length, scenario.conditional ? 1 : 0);
      const audit = (await f.bridge({ operation: "audit", detailed: true })).body;
      assert.equal(audit.field_count, 9); assert.equal(audit.next_index, 8); assert.equal(audit.charged_credits, 0);
      assert.equal(audit.receipt, null); assert.equal(audit.reservation_count, 0);
    } finally { await f.close(); }
  });
}

test("installed MV3 v2 cancels reviewed DOM drift before a signed field lease", async () => {
  const f = await fixture({ typed: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    await f.portal.locator(".select button").first().evaluate((button) => button.setAttribute("data-recipient", "changed-after-review"));
    const result = await message(f.popup, { type: "assistance_fill" }); assert.equal(result.ok, false);
    assert.equal(f.leases.length, 0);
    assert.deepEqual(await f.portal.evaluate(() => window.events), { input: [], change: [], blur: [], submit: 0, choice: [], opens: [] });
    const cancelled = await message(f.popup, { type: "assistance_cancel" }); assert.equal(cancelled.ok, true, cancelled.error);
    const audit = (await f.bridge({ operation: "audit", detailed: true })).body;
    assert.equal(audit.review_state, "cancelled"); assert.equal(audit.next_index, 0); assert.equal(audit.field_count, 0);
  } finally { await f.close(); }
});

test("installed MV3 v2 lost begin acknowledgement never mutates or replays the first field", async () => {
  const f = await fixture({ typed: true, loseBegin: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    const first = await message(f.popup, { type: "assistance_fill" }); assert.equal(first.ok, false);
    const state = await message(f.popup, { type: "status" }); assert.equal(state.value.phase, "unknown"); assert.equal(state.value.filled_count, 0);
    const retry = await message(f.popup, { type: "assistance_fill" }); assert.equal(retry.ok, false);
    assert.equal(f.leases.length, 1); assert.equal(f.requests.filter(({ url }) => url.endsWith("/begin")).length, 1);
    assert.deepEqual(await f.portal.evaluate(() => window.events), { input: [], change: [], blur: [], submit: 0, choice: [], opens: [] });
    const audit = (await f.bridge({ operation: "audit", detailed: true })).body;
    assert.equal(audit.field_count, 1); assert.equal(audit.next_index, 0); assert.equal(audit.receipt, null); assert.equal(audit.charged_credits, 0);
  } finally { await f.close(); }
});
