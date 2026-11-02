// Installed MV3 → signed device transport → actual isolated SQL/crypto service
// → actual Chrome document adapter. Every HTTPS request is locally fulfilled;
// the candidate approval calls use owned synthetic user 1, not provider access.
import assert from "node:assert/strict";
import test from "node:test";
import { cp, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { createHash } from "node:crypto";
import { chromium, expect } from "@playwright/test";
import { CONFIG as shippingConfig } from "../extension/config.js";
import { fixtureHtml } from "../fixtures/razorpay/form.js";
import { hostedFixtureHtml } from "../fixtures/razorpay/hosted-form.js";
import { submitFixtureHtml } from "../fixtures/razorpay/submit-form.js";
import { typedFixtureHtml } from "../fixtures/razorpay/typed-form.js";
import { legacySubmissionDispatch } from "./legacy-submission-dispatch-fixture.js";

const ROOT = new URL("../../", import.meta.url);
const WEBSITE = "https://www.hirewizhq.com";
const PORTAL = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005";
const EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb";
const message = (popup, value) => popup.evaluate((input) => chrome.runtime.sendMessage(input), value);
async function fixture({ granted = true, loseBegin = false, typed = false, unknownOption = false, conditional = false, tamperChoiceLease = false,
  attachment = false, tamperBinary = false, loseArtifact = false, loseAttachmentOutcome = false, attachmentFault = null, tamperAttachmentLease = false,
  pauseArtifact = false, attachmentConditional = false, hosted = false, hostedRows = 1, wrongCountryFlag = false, currentRoleConditional = true, submission = false, loseSubmitOutcome = false, tamperSubmitLease = false } = {}) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-reviewed-assistance-`);
  const pending = []; const requests = []; const leases = []; const uploads = []; let errors = ""; let context;
  let releaseArtifact; let signalArtifact;
  const artifactStarted = new Promise((resolve) => { signalArtifact = resolve; });
  const artifactGate = pauseArtifact ? new Promise((resolve) => { releaseArtifact = resolve; }) : null;
  const python = spawn(process.env.HIREWIZ_BROWSER_ASSISTANCE_PYTHON || new URL("backend/.venv/bin/python", ROOT).pathname,
    ["-I", new URL(submission ? "backend/tests/browser_submit_chrome_bridge.py" : "backend/tests/browser_assistance_chrome_bridge.py", ROOT).pathname],
    { cwd: new URL("backend/", ROOT).pathname, stdio: ["pipe", "pipe", "pipe"], env: {...process.env,PYTHONWARNINGS:"ignore::DeprecationWarning",PYTHONDONTWRITEBYTECODE:"1"} });
  python.stderr.on("data", (chunk) => { errors += chunk.toString(); });
  createInterface({ input: python.stdout }).on("line", (line) => pending.shift()?.resolve(JSON.parse(line)));
  python.on("exit", () => { for (const waiter of pending.splice(0)) waiter.reject(new Error("Owned SQL bridge exited")); });
  const bridge = (value) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Owned SQL bridge timed out")), 10000);
    pending.push({ resolve: (reply) => { clearTimeout(timer); resolve(reply); }, reject: (failure) => { clearTimeout(timer); reject(failure); } });
    python.stdin.write(`${JSON.stringify(value)}\n`);
  });
  try {
    const typedApplication = submission ? await bridge({operation:"configure_submit"}) : hosted ? await bridge({ operation: "configure_hosted", rows: hostedRows }) : attachment ? await bridge({ operation: "configure_attachment" }) : typed ? await bridge({ operation: "configure_typed" }) : null;
    if (typed || attachment || hosted || submission) assert.equal(typedApplication.status, 200);
    const extension = `${directory}/extension`;
    await cp(new URL("../extension/", import.meta.url), extension, { recursive: true });
    if (submission) await writeFile(`${extension}/background.js`, legacySubmissionDispatch(await readFile(`${extension}/background.js`, "utf8")));
    // This bridge intentionally exercises the legacy review cohort. Native
    // pairing uses its separate real SQL/cryptographic bridge; the shipping
    // configuration keeps its actual public key and connection requirement.
    await writeFile(`${extension}/config.js`,
      `export const CONFIG = Object.freeze(${JSON.stringify({ ...shippingConfig, nativePairing: null })});\n`);
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
        const html = submission ? submitFixtureHtml(typedApplication.body.field_mapping,typedApplication.body.answers,PORTAL) : hosted ? hostedFixtureHtml(typedApplication.body.field_mapping, PORTAL, { extraConditional: conditional, wrongCountryFlag, currentRoleConditional }) : typed || attachment ? typedFixtureHtml(typedApplication.body.field_mapping, PORTAL, { unknownOption, conditional, attachmentUpload: attachment, attachmentConditional })
          : fixtureHtml().replace(/<main[^>]*>/, "<main>").replace(/<script type="module">[\s\S]*?<\/script>/,
          `<script>window.events={input:[],change:[],blur:[],submit:0};document.querySelector('form').addEventListener('input',e=>events.input.push(e.target.id));document.querySelector('form').addEventListener('change',e=>events.change.push(e.target.id));document.querySelector('#email').addEventListener('blur',()=>events.blur.push('email'));document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();events.submit++});</script>`)
          .replace('action="/fixture/razorpay-like/apply"', `action="${PORTAL}"`);
        return route.fulfill({ status: 200, contentType: "text/html", body: html });
      }
      if ((attachment || submission) && url === `${PORTAL}/owned-synthetic-upload` && request.method() === "POST") {
        uploads.push(request.postDataBuffer());
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ owned_synthetic_only: true }) });
      }
      if (submission && url===`${PORTAL}/owned-synthetic-submit` && request.method()==="POST") return route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({owned_synthetic_ack:true})});
      if (submission && url===`${PORTAL}/confirmation` && request.method()==="GET") return route.fulfill({status:200,contentType:"text/html",body:'<div class="confirmation"><div class="confirmation__content">Thank you</div></div>'});
      if (url.startsWith(`${WEBSITE}/api/browser-assistance/device/`) && request.method() === "POST") {
        const origin = request.headers().origin;
        if (pauseArtifact && url.endsWith("/artifact")) { signalArtifact(); await artifactGate; }
        if (attachmentFault && url.endsWith("/artifact")) assert.equal((await bridge({ operation: "attachment_fault", fault: attachmentFault })).status, 200);
        const result = await bridge({ operation: "device", origin: origin || "", body: request.postDataJSON() });
        if (url.endsWith("/begin") && result.status === 200 && result.body.lease) {
          leases.push(structuredClone(result.body.lease));
          if (tamperChoiceLease && result.body.lease.payload.control_type === "combobox") {
            // Alter the actual signed label in transport, keeping its signature.
            result.body.lease.payload.option_label = "Unapproved transport choice";
          }
          if (tamperSubmitLease && result.body.lease.payload.review_protocol_version === 4) result.body.lease.payload.fee_credits=0;
          if (tamperAttachmentLease && result.body.lease.payload.review_protocol_version === 3) result.body.lease.payload.artifact.filename = "Unapproved.pdf";
        }
        if (loseBegin && (!submission || result.body.lease?.payload.review_protocol_version === 4) && !dropped && url.endsWith("/begin") && result.status === 200) { dropped = true; return route.abort("failed"); }
        if ((loseAttachmentOutcome || loseSubmitOutcome && result.body.review_protocol_version === 4) && !dropped && url.endsWith("/outcome") && result.status === 200) { dropped = true; return route.abort("failed"); }
        if (result.body.binary_base64) {
          if (loseArtifact) return route.abort("failed");
          const bytes = Buffer.from(result.body.binary_base64, "base64");
          if (tamperBinary) bytes[0] ^= 1;
          return route.fulfill({ status: 200, headers: { "Content-Type": result.body.descriptor.media_type,
            "Content-Length": String(bytes.length), "X-Artifact-SHA256": result.body.descriptor.sha256,
            "X-Artifact-Filename": result.body.descriptor.filename }, body: bytes });
        }
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
      await popup.locator("#assistance-application").fill(submission ? "apply_owned" : "apply_test");
      await popup.locator(attachment || submission ? "#assistance-prepare-attachment" : "#assistance-prepare").click();
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
    return { popup, portal, worker, context, bridge, requests, leases, uploads, artifactStarted, releaseArtifact,
      typedApplication: typedApplication?.body, connect, prepare, approve,
      async close() {
        await context?.close(); assert.equal(context.pages().length, 0);
        python.stdin.end(); await new Promise((resolve) => python.once("exit", resolve));
        assert.equal(errors, "");
        await rm(directory, { recursive: true, force: true }); await assert.rejects(stat(directory), { code: "ENOENT" });
        assert.ok(requests.every(({ url, method }) => url === PORTAL && method === "GET"
          || (attachment || submission) && url === `${PORTAL}/owned-synthetic-upload` && method === "POST"
          || submission && url === `${PORTAL}/owned-synthetic-submit` && method === "POST"
          || submission && url === `${PORTAL}/confirmation` && method === "GET"
          || url.startsWith(`${WEBSITE}/api/browser-assistance/device/`) && method === "POST"), "All requests are owned local fulfillments; no employer or processor requests");
      } };
  } catch (error) { await context?.close(); python.kill(); await rm(directory, { recursive: true, force: true }); throw error; }
}

test("installed MV3 legacy cohort fixture proves actual extension Origin and reviewed SQL/crypto fill-only flow", async () => {
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
      charged_credits: 0, application_status: "ready", field_count: 12, review_state: "filled", next_index: 12, review_protocol_version: 2, receipt: null, reservation_count: 0,
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

test("installed MV3 v3 approves exact binary disclosure before a one-shot resume selection without submission or fee", async () => {
  const f = await fixture({ attachment: true }); try {
    await f.connect(); const review = await f.prepare();
    assert.equal(review.review_protocol_version, 3); assert.equal(review.payload.review_protocol_version, 3);
    assert.deepEqual(review.payload.allowed_actions, ["attach"]); assert.deepEqual(review.payload.fields, []);
    assert.deepEqual(review.payload.attachment, { input_id: "resume", expected_empty: true });
    assert.equal(review.payload.artifact.resume_choice, "original");
    assert.ok(review.payload.disclosures.some((text) => /connected browser device/.test(text)));
    assert.ok(review.payload.disclosures.some((text) => /immediately upload or disclose/.test(text)));
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    assert.equal(f.requests.filter(({ url }) => url.endsWith("/artifact")).length, 0); assert.equal(f.leases.length, 0);
    assert.equal(await f.portal.locator("#resume").evaluate((node) => node.files.length), 0);
    await f.approve(review);
    const result = await message(f.popup, { type: "assistance_fill" }); assert.equal(result.ok, true, result.error);
    assert.equal(result.value.phase, "filled"); assert.equal(result.value.filled_count, 1);
    assert.match(result.value.message, /selected locally/); assert.match(result.value.message, /upload acceptance is unconfirmed/);
    await f.portal.evaluate(() => window.uploadPromise);
    const events = await f.portal.evaluate(() => window.events);
    assert.deepEqual(events, { input: ["resume"], change: ["resume"], blur: [], submit: 0, choice: [], opens: [] });
    const file = await f.portal.locator("#resume").evaluate((node) => ({ name: node.files[0].name, type: node.files[0].type, size: node.files[0].size }));
    assert.deepEqual(file, { name: review.payload.artifact.filename, type: review.payload.artifact.media_type, size: review.payload.artifact.size_bytes });
    assert.equal(f.uploads.length, 1);
    assert.equal(createHash("sha256").update(f.uploads[0]).digest("hex"), review.payload.artifact.sha256);
    assert.equal(f.leases.length, 1); const lease = f.leases[0].payload;
    assert.deepEqual(lease.artifact, review.payload.artifact); assert.equal(lease.control_type, "file");
    assert.deepEqual(lease.allowed_actions, ["attach"]); assert.equal(lease.document_id, review.payload.document_id);
    assert.equal(lease.review_digest, review.review_digest); assert.equal(lease.field_id, "resume");
    assert.equal(f.requests.filter(({ url }) => url.endsWith("/artifact")).length, 1);
    const audit = (await f.bridge({ operation: "audit", detailed: true, attachment: true })).body;
    assert.deepEqual(audit, { charged_credits: 0, application_status: "ready", field_count: 0, review_state: "filled", next_index: 1,
      review_protocol_version: 3, receipt: null, reservation_count: 0, attachment_count: 1, delivery_count: 1, delivery_claimed: true });
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    assert.equal(f.leases.length, 1); assert.equal(f.uploads.length, 1);
    const stored = await f.worker.evaluate(async () => (await chrome.storage.local.get("assistance_checkpoint")).assistance_checkpoint);
    for (const key of ["payload", "answers", "artifact", "artifact_base64"]) assert.equal(Object.hasOwn(stored, key), false);
  } finally { await f.close(); }
});

for (const scenario of [{ tamperBinary: true }, { loseArtifact: true }, { tamperAttachmentLease: true }]) {
  test(`installed MV3 v3 refuses uncertain/tampered binary without selection or resend: ${Object.keys(scenario)[0]}`, async () => {
    const f = await fixture({ attachment: true, ...scenario }); try {
      await f.connect(); const review = await f.prepare(); await f.approve(review);
      const first = await message(f.popup, { type: "assistance_fill" }); assert.equal(first.ok, false);
      if (scenario.tamperAttachmentLease) assert.match(first.error, /exact action signature could not be verified/);
      const state = await message(f.popup, { type: "status" }); assert.equal(state.value.phase, "unknown");
      assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
      assert.equal(f.leases.length, 1); assert.equal(f.uploads.length, 0);
      assert.equal(f.requests.filter(({ url }) => url.endsWith("/artifact")).length, scenario.tamperAttachmentLease ? 0 : 1);
      assert.equal(await f.portal.locator("#resume").evaluate((node) => node.files.length), 0);
      assert.equal((await f.bridge({ operation: "audit", detailed: true, attachment: true })).body.charged_credits, 0);
    } finally { await f.close(); }
  });
}

for (const attachmentFault of ["cancel", "revoke", "expired", "flag_off", "artifact_hash"]) {
  test(`installed MV3 v3 freshly refuses binary delivery after ${attachmentFault}`, async () => {
    const f = await fixture({ attachment: true, attachmentFault }); try {
      await f.connect(); const review = await f.prepare(); await f.approve(review);
      assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
      assert.equal((await message(f.popup, { type: "status" })).value.phase, "unknown");
      assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
      assert.equal(f.requests.filter(({ url }) => url.endsWith("/artifact")).length, 1);
      const audit = (await f.bridge({ operation: "audit", detailed: true, attachment: true })).body;
      assert.equal(audit.delivery_count, 0); assert.equal(audit.delivery_claimed, false); assert.equal(audit.review_protocol_version, 3);
      assert.equal(audit.charged_credits, 0); assert.equal(audit.receipt, null); assert.equal(audit.reservation_count, 0);
      assert.equal(f.uploads.length, 0); assert.equal(await f.portal.locator("#resume").evaluate((node) => node.files.length), 0);
    } finally { await f.close(); }
  });
}

test("installed MV3 v3 existing attachment stops before begin and never gets binary", async () => {
  const f = await fixture({ attachment: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    await f.portal.locator("#resume").setInputFiles({ name: "Other-synthetic.pdf", mimeType: "application/pdf", buffer: Buffer.from("Other synthetic file") });
    await f.portal.evaluate(() => window.uploadPromise);
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    assert.equal(f.leases.length, 0); assert.equal(f.requests.filter(({ url }) => url.endsWith("/artifact")).length, 0);
    assert.deepEqual(await f.portal.evaluate(() => window.events.input), ["resume"]);
    assert.equal(f.uploads.length, 1, "Only the explicitly simulated manual selection uploaded; no companion event followed");
  } finally { await f.close(); }
});

for (const change of ["file-input", "supported-answer", "destination"]) {
  test(`installed MV3 v3 changed ${change} blocks before attachment begin or binary disclosure`, async () => {
    const f = await fixture({ attachment: true }); try {
      await f.connect(); const review = await f.prepare(); await f.approve(review);
      await f.portal.evaluate((change) => {
        if (change === "file-input") document.getElementById("resume").name = "another-resume-input";
        else if (change === "supported-answer") document.getElementById("first_name").value = "Changed after review";
        else document.querySelector("form").action = "https://job-boards.greenhouse.io/another-tenant/jobs/123";
      }, change);
      assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
      assert.equal(f.leases.length, 0); assert.equal(f.requests.filter(({ url }) => url.endsWith("/artifact")).length, 0);
      assert.equal(f.uploads.length, 0); assert.deepEqual(await f.portal.evaluate(() => window.events.input), []);
    } finally { await f.close(); }
  });
}

test("installed MV3 v3 local cancellation during the already authorized binary request prevents file selection", async () => {
  const f = await fixture({ attachment: true, pauseArtifact: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    const filling = message(f.popup, { type: "assistance_fill" });
    await f.artifactStarted;
    const cancelled = await message(f.popup, { type: "assistance_cancel" }); assert.equal(cancelled.ok, true);
    f.releaseArtifact();
    const result = await filling; assert.equal(result.ok, true, result.error); assert.equal(result.value.phase, "cancelled");
    assert.equal(f.uploads.length, 0); assert.deepEqual(await f.portal.evaluate(() => window.events.input), []);
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    const audit = (await f.bridge({ operation: "audit", detailed: true, attachment: true })).body;
    assert.equal(audit.delivery_count, 1); assert.equal(audit.delivery_claimed, true); assert.equal(audit.receipt, null);
  } finally { f.releaseArtifact?.(); await f.close(); }
});

test("installed MV3 v3 conditional upload callback stays unknown and never selects/uploads again", async () => {
  const f = await fixture({ attachment: true, attachmentConditional: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    const first = await message(f.popup, { type: "assistance_fill" }); assert.equal(first.ok, true, first.error);
    assert.equal(first.value.phase, "unknown"); assert.doesNotMatch(first.value.message, /Exact resume selected locally/);
    await f.portal.evaluate(() => window.uploadPromise);
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    assert.equal(f.leases.length, 1); assert.equal(f.uploads.length, 1); assert.deepEqual(await f.portal.evaluate(() => window.events.input), ["resume"]);
    const audit = (await f.bridge({ operation: "audit", detailed: true, attachment: true })).body;
    assert.equal(audit.review_state, "unknown"); assert.equal(audit.receipt, null); assert.equal(audit.charged_credits, 0);
  } finally { await f.close(); }
});

test("installed MV3 v3 lost selection outcome acknowledgement never selects/uploads twice", async () => {
  const f = await fixture({ attachment: true, loseAttachmentOutcome: true }); try {
    await f.connect(); const review = await f.prepare(); await f.approve(review);
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    await f.portal.evaluate(() => window.uploadPromise);
    assert.equal((await message(f.popup, { type: "status" })).value.phase, "unknown");
    assert.equal((await message(f.popup, { type: "assistance_fill" })).ok, false);
    assert.equal(f.leases.length, 1); assert.equal(f.uploads.length, 1);
    assert.deepEqual(await f.portal.evaluate(() => window.events.input), ["resume"]);
    const audit = (await f.bridge({ operation: "audit", detailed: true, attachment: true })).body;
    assert.equal(audit.review_state, "filled"); assert.equal(audit.delivery_count, 1); assert.equal(audit.receipt, null);
  } finally { await f.close(); }
});

function hostedAnswers(rows = 1) {
  return Object.fromEntries([['country','in'],...Array.from({length:rows},(_,i)=>[
    [`company-name-${i}`,`Explicit synthetic company ${i}`],[`title-${i}`,'Synthetic engineer'],
    [`start-date-month-${i}`,'January'],[`start-date-year-${i}`,'2020'],
    [`end-date-month-${i}`,'December'],[`end-date-year-${i}`,'2022'],[`current-role-${i}_1`,'false']]).flat()]);
}
async function reviseHosted(f, review, values) {
  const result = await f.bridge({operation:'candidate',action:'revise',body:{identity:review.id,review_digest:review.review_digest,hosted_answers:values}});
  assert.equal(result.status,200); return result.body;
}

test('installed MV3 one review fills two exact employment rows and Phone Country through real SQL/crypto', async()=>{
  const f=await fixture({hosted:true,hostedRows:2});try{
    await f.connect();const draft=await f.prepare();assert.equal(draft.payload.hosted.rows.length,2);assert.equal(draft.payload.fields.length,12);
    const premature=await message(f.popup,{type:'assistance_fill'});assert.equal(premature.ok,false);assert.deepEqual(await f.portal.evaluate(()=>events.input),[]);
    const review=await reviseHosted(f,draft,hostedAnswers(2));assert.equal(review.payload.fields.length,25);assert.notEqual(review.review_digest,draft.review_digest);assert.equal(review.expires_at_ms,draft.expires_at_ms);
    await f.approve(review);const result=await message(f.popup,{type:'assistance_fill'});assert.equal(result.ok,true,result.error);assert.equal(result.value.phase,'filled');assert.equal(result.value.filled_count,25);
    assert.equal(f.leases.length,25);const events=await f.portal.evaluate(()=>window.events);assert.equal(events.submit,0);assert.equal(events.add,0);assert.deepEqual(events.current,[]);
    assert.deepEqual(events.choice.find(value=>value.id==='country'),{id:'country',value:'in',label:'India +91'});
    assert.equal(await f.portal.locator('#company-name-1').inputValue(),'Explicit synthetic company 1');assert.equal(await f.portal.locator('#start-date-year-1').inputValue(),'2020');
    assert.equal((await f.bridge({operation:'audit',detailed:true})).body.receipt,null);assert.equal((await f.bridge({operation:'audit'})).body.charged_credits,0);
    const stored=await f.worker.evaluate(async()=> (await chrome.storage.local.get('assistance_checkpoint')).assistance_checkpoint);assert.equal(stored.filled_count,25);assert.equal(Object.hasOwn(stored,'answers'),false);
  }finally{await f.close();}
});

test('installed MV3 current role changes stop for fresh inspection before any newly conditional action',async()=>{
  const f=await fixture({hosted:true});try{
    await f.connect();const draft=await f.prepare();const values=hostedAnswers();delete values['end-date-month-0'];delete values['end-date-year-0'];values['current-role-0_1']='true';values['employment-add-1']='2';
    const review=await reviseHosted(f,draft,values);await f.approve(review);const result=await message(f.popup,{type:'assistance_fill'});assert.equal(result.ok,true,result.error);assert.equal(result.value.phase,'unknown');
    assert.equal(await f.portal.locator('#current-role-0_1').isChecked(),true);assert.equal(await f.portal.locator('#end-date-year-0').count(),0);assert.equal(await f.portal.evaluate(()=>events.add),0);
    const before=f.leases.length;assert.equal((await message(f.popup,{type:'assistance_fill'})).ok,false);assert.equal(f.leases.length,before);
    await f.popup.locator('#assistance-cancel').click();await expect(f.popup.locator('#assistance-prepare')).toBeEnabled();const fresh=await f.prepare();assert.deepEqual(fresh.payload.hosted.rows,[{index:0,current_role:true}]);assert.equal(fresh.payload.field_mapping.some(field=>field.id==='end-date-year-0'),false);assert.equal(fresh.payload.hosted.draft_answers,undefined);
  }finally{await f.close();}
});

test('installed MV3 Add another is one exact approved click and new row requires a fresh review',async()=>{
  const f=await fixture({hosted:true});try{
    await f.connect();const draft=await f.prepare();const review=await reviseHosted(f,draft,{...hostedAnswers(),'employment-add-1':'2'});await f.approve(review);
    const result=await message(f.popup,{type:'assistance_fill'});assert.equal(result.ok,true,result.error);assert.equal(result.value.phase,'unknown');assert.equal(await f.portal.evaluate(()=>events.add),1);
    assert.equal(await f.portal.locator('#company-name-1').inputValue(),'');assert.equal(await f.portal.locator('#title-1').inputValue(),'');const count=f.leases.length;
    assert.equal((await message(f.popup,{type:'assistance_fill'})).ok,false);assert.equal(f.leases.length,count);assert.equal(await f.portal.evaluate(()=>events.add),1);
    await f.popup.locator('#assistance-cancel').click();await expect(f.popup.locator('#assistance-prepare')).toBeEnabled();const fresh=await f.prepare();assert.equal(fresh.payload.hosted.rows.length,2);assert.equal(fresh.payload.hosted.values['company-name-1'],'');assert.notEqual(fresh.payload.context_digest,review.payload.context_digest);
  }finally{await f.close();}
});

test('installed MV3 country code tamper opens once but selects no unapproved country or employment',async()=>{
  const f=await fixture({hosted:true,wrongCountryFlag:true});try{
    await f.connect();const review=await reviseHosted(f,await f.prepare(),hostedAnswers());await f.approve(review);const result=await message(f.popup,{type:'assistance_fill'});
    assert.equal(result.ok,true,result.error);assert.equal(result.value.phase,'unknown');assert.equal(await f.portal.locator('#company-name-0').inputValue(),'');
    assert.equal(await f.portal.evaluate(()=>events.choice.some(choice=>choice.id==='country')),false);assert.equal(await f.portal.evaluate(()=>events.opens.filter(id=>id==='country').length),1);
    assert.equal((await message(f.popup,{type:'assistance_fill'})).ok,false);
  }finally{await f.close();}
});

for(const kind of ['manual-employment-change','changed-employment-year-bound','changed-add-target'])test(`installed MV3 ${kind} cancels exact review before first disclosure`,async()=>{
  const f=await fixture({hosted:true});try{
    await f.connect();const review=await reviseHosted(f,await f.prepare(),{...hostedAnswers(),'employment-add-1':'2'});await f.approve(review);
    await f.portal.evaluate(kind=>{if(kind==='manual-employment-change')document.getElementById('company-name-0').value='Unreviewed edit';if(kind==='changed-employment-year-bound')document.getElementById('start-date-year-0').maxLength=5;if(kind==='changed-add-target')document.querySelector('.add-another-button').textContent='Apply now';},kind);
    assert.equal((await message(f.popup,{type:'assistance_fill'})).ok,false);assert.equal(f.leases.length,0);assert.deepEqual(await f.portal.evaluate(()=>events.input),[]);assert.equal(await f.portal.evaluate(()=>events.add),0);
  }finally{await f.close();}
});

test('installed MV3 current-role toggle always ends the sequence even with no conditional DOM callback',async()=>{
  const f=await fixture({hosted:true,currentRoleConditional:false});try{
    await f.connect();const review=await reviseHosted(f,await f.prepare(),{'current-role-0_1':'true','employment-add-1':'2'});await f.approve(review);
    const result=await message(f.popup,{type:'assistance_fill'});assert.equal(result.ok,true,result.error);assert.equal(result.value.phase,'unknown');
    assert.equal(await f.portal.locator('#current-role-0_1').isChecked(),true);assert.equal(await f.portal.locator('#end-date-year-0').count(),1);assert.equal(await f.portal.evaluate(()=>events.add),0);
    assert.equal((await message(f.popup,{type:'assistance_fill'})).ok,false);assert.equal(await f.portal.evaluate(()=>events.current.length),1);
  }finally{await f.close();}
});


async function finalReview(f) {
  await f.connect(); const attachment=await f.prepare(); await f.approve(attachment);
  const selected=await message(f.popup,{type:"assistance_fill"}); assert.equal(selected.ok,true,selected.error); assert.equal(selected.value.phase,"filled");
  await expect(f.portal.locator('.file-upload__filename p')).toHaveText('Owned.pdf');
  assert.equal(f.uploads.length,1); assert.equal(createHash('sha256').update(f.uploads[0]).digest('hex'),attachment.payload.artifact.sha256);
  assert.equal((await message(f.popup,{type:"assistance_cancel"})).ok,true);
  const result=await message(f.popup,{type:"assistance_prepare_submit",application_id:"apply_owned"}); assert.equal(result.ok,true,result.error);
  const review=await f.bridge({operation:"candidate",action:"review",body:{identity:result.value.review_id}}); assert.equal(review.status,200);
  assert.equal(review.body.payload.fee_credits,20); assert.deepEqual(review.body.payload.allowed_actions,["submit"]);
  return review.body;
}
test("installed MV3 v4 binds actual exact-file selection, complete review, 20-credit hold and one final click; public confirmation never settles",async()=>{
  const f=await fixture({submission:true}); try {
    const review=await finalReview(f);
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    assert.equal((await f.bridge({operation:"audit"})).body.credits,100);
    await f.approve(review); assert.equal((await f.bridge({operation:"audit"})).body.credits,80);
    const sent=await message(f.popup,{type:"assistance_fill"}); assert.equal(sent.ok,true,sent.error); assert.equal(sent.value.phase,"unknown");
    await f.portal.waitForURL(PORTAL+'/confirmation');
    assert.equal(f.requests.filter(({url})=>url.endsWith('/owned-synthetic-submit')).length,1);
    assert.equal(f.leases.filter(({payload})=>payload.review_protocol_version===4).length,1);
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    const inspected=await message(f.popup,{type:"assistance_reconcile"}); assert.equal(inspected.ok,true,inspected.error); assert.match(inspected.value.message,/does not verify receipt/);
    const audit=(await f.bridge({operation:"audit"})).body;
    assert.equal(audit.native_calls,1); assert.equal(audit.submit_actions,1); assert.equal(audit.attempts,1); assert.equal(audit.receipt,null); assert.equal(audit.charged_credits,0);
    assert.deepEqual(audit.reservations,[{state:'reserved',reserved:20,committed:0,released:0}]);
  } finally {await f.close();}
});
for(const fault of ['loseBegin','loseSubmitOutcome','tamperSubmitLease']) test(`installed MV3 v4 ${fault} never repeats native reserve or final click`,async()=>{
  const f=await fixture({submission:true,[fault]:true}); try {
    const review=await finalReview(f); await f.approve(review);
    const result=await message(f.popup,{type:"assistance_fill"}); assert.equal(result.ok,false);
    assert.equal((await message(f.popup,{type:"status"})).value.phase,'unknown');
    assert.equal((await message(f.popup,{type:"assistance_fill"})).ok,false);
    const clicks=f.requests.filter(({url})=>url.endsWith('/owned-synthetic-submit')).length;
    assert.equal(clicks,fault==='loseSubmitOutcome'?1:0);
    const audit=(await f.bridge({operation:"audit"})).body;
    assert.equal(audit.native_calls,1); assert.equal(audit.submit_actions,1); assert.equal(audit.credits,80); assert.equal(audit.receipt,null);
  } finally {await f.close();}
});
test("installed MV3 v4 changed answer after final approval stops before native claim or click",async()=>{
  const f=await fixture({submission:true});try {
    const review=await finalReview(f);await f.approve(review);
    await f.portal.locator('#first_name').fill('Changed after review');
    const result=await message(f.popup,{type:'assistance_fill'});assert.equal(result.ok,false);
    assert.equal(f.requests.filter(({url})=>url.endsWith('/owned-synthetic-submit')).length,0);
    assert.equal((await f.bridge({operation:'audit'})).body.native_calls,0);
    assert.equal((await message(f.popup,{type:'assistance_cancel'})).ok,true);
    assert.equal((await f.bridge({operation:'audit'})).body.credits,100);
  }finally{await f.close();}
});

test('installed MV3 v4 rejects different manually uploaded bytes with identical approved filename in same document',async()=>{
  const f=await fixture({submission:true});try {
    const review=await finalReview(f);await f.approve(review);
    await f.portal.locator('.file-upload').evaluate(group=>{group.innerHTML='<label for="resume">Attach</label><input class="visually-hidden" id="resume" type="file">';});
    await f.portal.locator('#resume').setInputFiles({name:'Owned.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\nDIFFERENT OWNED SYNTHETIC BYTES\n%%EOF')});
    await expect(f.portal.locator('.file-upload__filename p')).toHaveText('Owned.pdf');
    assert.equal(f.uploads.length,2);assert.notEqual(createHash('sha256').update(f.uploads[1]).digest('hex'),review.payload.artifact.sha256);
    const result=await message(f.popup,{type:'assistance_fill'});assert.equal(result.ok,false);
    assert.equal(f.requests.filter(({url})=>url.endsWith('/owned-synthetic-submit')).length,0);
    assert.equal((await f.bridge({operation:'audit'})).body.native_calls,0);
    assert.equal((await message(f.popup,{type:'assistance_cancel'})).ok,true);
    assert.equal((await f.bridge({operation:'audit'})).body.credits,100);
  }finally{await f.close();}
});
