// Chrome DOM integration against locally fulfilled, hand-authored public-shape
// HTML. No employer fetch, vendor bundle, candidate data or submission occurs.
import assert from "node:assert/strict";
import test from "node:test";
import { cp, mkdir, mkdtemp, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import { chromium } from "@playwright/test";
import { fixtureHtml } from "../fixtures/razorpay/form.js";

const portal = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005";
const answers = Object.freeze({ first_name: "Synthetic Alice", last_name: "Fixture", email: "synthetic@example.test", question_8970454005: "https://example.test/profile", question_8970455005: "https://example.test/site", question_8970456005: "10", question_8970461005: "Synthetic Bengaluru" });
const historicalMapping = Object.freeze([
  { id: "first_name", label: "First Name", type: "text", required: true }, { id: "last_name", label: "Last Name", type: "text", required: true }, { id: "email", label: "Email", type: "text", required: true },
  { id: "question_8970454005", label: "LinkedIn Profile", type: "text", required: true }, { id: "question_8970455005", label: "Website", type: "text", required: true },
  { id: "question_8970456005", label: "Total Years of Experience", type: "text", required: true }, { id: "question_8970461005", label: "Current Location", type: "text", required: true },
]);
// IDs/required text semantics from the current anonymous public question API.
// The corresponding HTML below is hand authored; live SSR compatibility remains
// a separate read-only acceptance check, never implied by this fixture.
const currentPortal = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4739199005";
const currentMapping = Object.freeze([...historicalMapping.slice(0, 3),
  { id: "question_9123507005", label: "LinkedIn Profile", type: "text", required: true },
  { id: "question_9123508005", label: "Total Years of Experience", type: "text", required: true },
]);
const currentAnswers = Object.freeze({ first_name: answers.first_name, last_name: answers.last_name, email: answers.email,
  question_9123507005: "https://example.test/current-profile", question_9123508005: "10" });
const canonical = (value) => value === null || typeof value !== "object" ? JSON.stringify(value) : Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
const hash = (value) => createHash("sha256").update(value).digest("hex");
const recorder = `<script>
window.observed={input:[],change:[],blur:[],submit:0,file:0};
document.querySelector('form').addEventListener('input',e=>window.observed.input.push(e.target.id));
document.querySelector('form').addEventListener('change',e=>{window.observed.change.push(e.target.id);if(e.target.type==='file')window.observed.file++;});
document.querySelector('#email').addEventListener('blur',e=>window.observed.blur.push(e.target.value));
document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.observed.submit++;});
</script>`;
function publicShape() {
  // Deliberately remove all synthetic root identity/policy markers. The adapter
  // must derive its context from actual DOM structure, never fixture attestation.
  return fixtureHtml().replace(/<main[^>]*>/, "<main>").replace(/<script type="module">[\s\S]*?<\/script>/, recorder)
    .replace('action="/fixture/razorpay-like/apply"', `action="${portal}"`);
}
function currentMetadataShape() {
  const fields = currentMapping.map(({ id, label }) => `<label for="${id}">${label}*</label><input id="${id}" type="text" maxlength="255" aria-required="true">`).join("");
  const manual = [["question_9123509005", "Gender"], ["question_9123510005", "Current Company"], ["question_9123511005", "Designation"], ["question_9123512005", "Office Bengaluru"]]
    .map(([id, label]) => `<label for="${id}">${label}*</label><input id="${id}" type="text" role="combobox" aria-required="true">`).join("");
  return `<!doctype html><html><head><title>Local current metadata DOM reference</title><style>label,input{display:block;margin:8px}</style></head><body><form action="${currentPortal}" method="get">${fields}<label for="phone">Phone*</label><input id="phone" type="tel" aria-required="true"><label for="resume">Resume/CV*</label><input id="resume" type="file" aria-required="true">${manual}<button type="submit">Submit manually</button></form>${recorder}</body></html>`;
}
async function fixture({ mapping = historicalMapping, html = publicShape(), values = answers, targetUrl = portal } = {}) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-razorpay-dom-`); let context;
  const requests = []; const errors = [];
  try {
    const extension = `${directory}/extension`;
    await mkdir(extension);
    await cp(new URL("../extension/razorpay-adapter.js", import.meta.url), `${extension}/razorpay-adapter.js`);
    await writeFile(`${extension}/manifest.json`, JSON.stringify({ manifest_version: 3, name: "Synthetic adapter DOM test", version: "1.0", permissions: ["scripting"], host_permissions: ["https://job-boards.greenhouse.io/*"], background: { service_worker: "background.js", type: "module" } }));
    await writeFile(`${extension}/background.js`, "import { razorpayAdapter } from './razorpay-adapter.js'; globalThis.adapter = razorpayAdapter;");
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: "chromium", headless: true, serviceWorkers: "allow", args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    await context.route("**/*", async (route) => {
      requests.push({ url: route.request().url(), method: route.request().method() });
      if (route.request().isNavigationRequest() && route.request().method() === "GET" && route.request().url() === targetUrl) return route.fulfill({ status: 200, contentType: "text/html", body: html });
      return route.abort();
    });
    const page = await context.newPage(); page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(targetUrl); await page.waitForFunction(() => Boolean(window.observed));
    const tab = await worker.evaluate(async (url) => (await chrome.tabs.query({ url }))[0].id, targetUrl);
    const invoke = async (expected = null, action = null, suppliedIdentity = undefined) => worker.evaluate(async ({ tab, mapping, expected, action, suppliedIdentity }) => {
      const [probe] = await chrome.scripting.executeScript({ target: { tabId: tab, frameIds: [0] }, func: () => null });
      const identity = suppliedIdentity === undefined ? { document_id: probe.documentId, frame_id: probe.frameId, field_mapping: mapping } : suppliedIdentity;
      const [result] = await chrome.scripting.executeScript({ target: { tabId: tab, documentIds: [probe.documentId] }, func: globalThis.adapter, args: [expected, action, identity] });
      return { result: result.result, chrome_document_id: result.documentId, chrome_frame_id: result.frameId };
    }, { tab, mapping, expected, action, suppliedIdentity });
    const read = await invoke(); const review = read.result;
    const reviewed = { ...review, reviewed_answers: values, review_digest: hash("Synthetic native authority reviewed package; not server authorization"),
      reviewed_answers_sha256: hash(canonical({ context_digest: review.context_digest, reviewed_answers: values })) };
    const expectedValues = { ...review.assisted_values };
    const fill = async (field = "first_name", extra = {}, expected = reviewed) => invoke({ ...expected, current_assisted_values: expected.current_assisted_values ?? expectedValues }, {
      field_id: field, value: values[field], expected_before: "", deadline: Date.now() + 1500,
      document_id: expected.document_id, frame_id: 0, context_digest: expected.context_digest,
      review_digest: expected.review_digest, reviewed_answers_sha256: expected.reviewed_answers_sha256, ...extra,
    }).then(({ result }) => { if (result.outcome === "filled_only") expectedValues[field] = values[field]; return result; });
    const noWrite = async () => assert.deepEqual(await page.evaluate(() => window.observed), { input: [], change: [], blur: [], submit: 0, file: 0 });
    return { page, worker, tab, review, reviewed, read, invoke, fill, noWrite,
      async close() { await context.close(); assert.equal(context.pages().length, 0); await rm(directory, { recursive: true, force: true }); await assert.rejects(stat(directory), { code: "ENOENT" }); assert.deepEqual(requests, [{ url: targetUrl, method: "GET" }], "All portal HTML was locally fulfilled; no employer or processor requests"); assert.deepEqual(errors, []); } };
  } catch (error) { await context?.close(); await rm(directory, { recursive: true, force: true }); throw error; }
}

test("actual Chrome document identity, real-shape SSR context, full manual handoff and read-only inspection", async () => {
  const f = await fixture(); try {
    assert.equal(f.review.outcome, "inspected");
    assert.equal(f.review.document_id, f.read.chrome_document_id); assert.equal(f.read.chrome_frame_id, 0);
    assert.match(f.review.context_digest, /^[a-f0-9]{64}$/); assert.equal(f.review.assisted_fields.length, 7);
    assert.deepEqual(f.review.assisted_values, Object.fromEntries(Object.keys(answers).map((id) => [id, ""])));
    assert.equal(await f.page.locator("[data-hirewiz-scaffold]").count(), 0);
    assert.ok(f.review.required_controls > 8);
    for (const id of ["country", "phone", "company-name-0", "question_8970458005", "resume"]) assert.ok(f.review.manual_fields.some((field) => field.id === id));
    assert.equal(f.review.context.recipient_disclosure.email.before_submit, true);
    assert.match(f.review.message, /final submission remain manual/); await f.noWrite();
    assert.equal((await f.invoke(null, null, null)).result.outcome, "blocked");
  } finally { await f.close(); }
});

test("current public question IDs use only the supplied five-field semantic mapping", async () => {
  const f = await fixture({ mapping: currentMapping, html: currentMetadataShape(), values: currentAnswers, targetUrl: currentPortal }); try {
    assert.equal(f.review.outcome, "inspected"); assert.equal(f.review.assisted_fields.length, 5);
    assert.deepEqual(f.review.field_mapping, currentMapping); assert.equal(f.review.canonical_opening_key, "4739199005");
    assert.equal(hash(canonical(f.review.context)), f.review.context_digest);
    assert.equal((await f.fill("question_9123507005")).outcome, "filled_only");
    assert.equal((await f.fill("question_9123508005")).outcome, "filled_only");
    for (const id of ["question_9123509005", "question_9123510005", "question_9123511005", "question_9123512005", "phone", "resume"]) assert.ok(f.review.manual_fields.some((field) => field.id === id));
    assert.deepEqual(await f.page.evaluate(() => window.observed), { input: ["question_9123507005", "question_9123508005"], change: ["question_9123507005", "question_9123508005"], blur: [], submit: 0, file: 0 });
  } finally { await f.close(); }
});

test("a signed three-field base mapping leaves unmapped custom controls manual", async () => {
  const f = await fixture({ mapping: historicalMapping.slice(0, 3), values: { first_name: answers.first_name, last_name: answers.last_name, email: answers.email } }); try {
    assert.equal(f.review.outcome, "inspected"); assert.equal(f.review.assisted_fields.length, 3);
    assert.ok(f.review.manual_fields.some((field) => field.id === "question_8970454005"));
    assert.equal((await f.fill()).outcome, "filled_only");
    assert.equal((await f.fill("question_8970454005", { value: "https://example.test/unapproved" })).outcome, "blocked");
  } finally { await f.close(); }
});

test("foreign, sensitive, duplicate, missing and malformed application mappings never authorize inspection or filling", async () => {
  const f = await fixture(); try {
    const invalid = [null, [], historicalMapping.slice(0, 2),
      historicalMapping.map((field, index) => index === 0 ? { ...field, id: "foreign_first_name" } : field),
      historicalMapping.map((field, index) => index === 3 ? { ...field, label: "Gender" } : field),
      historicalMapping.map((field, index) => index === 3 ? { ...field, id: "foreign_custom_id" } : field),
      historicalMapping.map((field, index) => index === 3 ? { ...field, type: "select-one" } : field),
      historicalMapping.map((field, index) => index === 3 ? { ...field, required: "true" } : field),
      historicalMapping.map((field, index) => index === 3 ? historicalMapping[4] : field),
      [...historicalMapping, historicalMapping[3]],
    ];
    for (const mapping of invalid) {
      const result = await f.invoke(null, null, { document_id: f.review.document_id, frame_id: 0, field_mapping: mapping });
      assert.equal(result.result.outcome, "blocked"); await f.noWrite();
    }
    const reordered = [historicalMapping[1], historicalMapping[0], ...historicalMapping.slice(2)];
    const result = await f.invoke(f.reviewed, { field_id: "first_name", value: answers.first_name, expected_before: "", deadline: Date.now() + 1000,
      document_id: f.review.document_id, frame_id: 0, context_digest: f.review.context_digest, review_digest: f.reviewed.review_digest, reviewed_answers_sha256: f.reviewed.reviewed_answers_sha256 },
    { document_id: f.review.document_id, frame_id: 0, field_mapping: reordered });
    assert.equal(result.result.outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});

test("two sequential approved fills preserve context; native setter fires only text events", async () => {
  const f = await fixture(); try {
    const repeated = (await f.invoke()).result;
    const differences = [];
    const compare = (a, b, path = "context") => {
      if (a === b) return;
      if (a && b && typeof a === "object" && typeof b === "object") { for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) compare(a[key], b[key], `${path}.${key}`); }
      else differences.push({ path, before: a, after: b });
    };
    compare(f.review.context, repeated.context); assert.deepEqual(differences, []);
    assert.equal(repeated.context_digest, f.review.context_digest);
    const first = await f.fill(); assert.equal(first.outcome, "filled_only", JSON.stringify(first));
    assert.equal((await f.fill("last_name")).outcome, "filled_only");
    assert.equal((await f.fill("first_name")).outcome, "blocked");
    assert.equal((await f.invoke()).result.context_digest, f.review.context_digest);
    assert.equal((await f.invoke()).result.assisted_values.first_name, answers.first_name);
    assert.deepEqual(await f.page.evaluate(() => window.observed), { input: ["first_name", "last_name"], change: ["first_name", "last_name"], blur: [], submit: 0, file: 0 });
    assert.equal(await f.page.locator("#first_name").inputValue(), answers.first_name);
    assert.equal(await f.page.locator("#last_name").inputValue(), answers.last_name);
    const result = await f.fill("question_8970454005"); assert.ok(result.manual_handoff.some((field) => field.id === "email"));
    for (const id of ["resume", "country", "phone", "question_8970458005"]) assert.ok(result.manual_handoff.some((field) => field.id === id));
  } finally { await f.close(); }
});

test("one-field leases permit up to ten seconds while expired and overlong deadlines cause no events", async () => {
  const f = await fixture(); try {
    assert.equal((await f.fill("first_name", { deadline: Date.now() - 1 })).outcome, "blocked"); await f.noWrite();
    assert.equal((await f.fill("first_name", { deadline: Date.now() + 20000 })).outcome, "blocked"); await f.noWrite();
    assert.equal((await f.fill("first_name", { deadline: Date.now() + 9000 })).outcome, "filled_only");
    assert.deepEqual(await f.page.evaluate(() => window.observed), { input: ["first_name"], change: ["first_name"], blur: [], submit: 0, file: 0 });
  } finally { await f.close(); }
});

test("known email blur requires explicit disclosure bound to the reviewed context", async () => {
  const f = await fixture(); try {
    assert.equal((await f.fill("email")).outcome, "blocked"); await f.noWrite();
    const disclosure = { acknowledged: true, event: "blur", recipient: "https://email-address-validator.us.greenhouse.io/address/validate", context_digest: f.review.context_digest };
    assert.equal((await f.fill("email", { email_blur_disclosure: { ...disclosure, context_digest: "0".repeat(64) } })).outcome, "blocked"); await f.noWrite();
    const email = await f.fill("email", { email_blur_disclosure: disclosure }); assert.equal(email.outcome, "filled_only", JSON.stringify(email));
    assert.deepEqual(await f.page.evaluate(() => window.observed), { input: ["email"], change: ["email"], blur: [answers.email], submit: 0, file: 0 });
  } finally { await f.close(); }
});

for (const mutation of ["manual-text", "manual-checkbox", "manual-hidden", "conditional", "moved", "duplicate", "hidden", "disabled", "label", "type", "max", "script", "manual-options", "manual-description", "query", "hash", "wrong-tenant", "login", "modal"]) test(`${mutation} invalidates exact review before DOM disclosure`, async () => {
  const f = await fixture(); try {
    await f.page.evaluate((mutation) => {
      const first = document.getElementById("first_name"); const form = first.form;
      if (mutation === "manual-text") document.getElementById("company-name-0").value = "Candidate manual change";
      if (mutation === "manual-checkbox") document.getElementById("current-role-0_1").checked = true;
      if (mutation === "manual-hidden") { const node = document.createElement("input"); node.type = "hidden"; node.name = "selection"; node.value = "different"; form.append(node); }
      if (mutation === "conditional") { const node = document.createElement("input"); node.id = "new_required_question"; node.required = true; form.append(node); }
      if (mutation === "moved") document.body.append(first);
      if (mutation === "duplicate") first.after(first.cloneNode());
      if (mutation === "hidden") first.parentElement.style.opacity = "0";
      if (mutation === "disabled") first.disabled = true;
      if (mutation === "label") first.labels[0].textContent = "Different meaning";
      if (mutation === "type") first.type = "email";
      if (mutation === "max") first.maxLength = 256;
      if (mutation === "script") { const node = document.createElement("script"); node.type = "application/json"; node.textContent = '{"recipient":"changed"}'; document.head.append(node); }
      if (mutation === "manual-options") document.getElementById("country").dataset.options = '[{"value":"different","label":"Different"}]';
      if (mutation === "manual-description") { const node = document.createElement("p"); node.textContent = "Changed consent context"; form.append(node); }
      if (mutation === "query") history.replaceState(null, "", "?source=changed");
      if (mutation === "hash") history.replaceState(null, "", "#changed");
      if (mutation === "wrong-tenant") history.replaceState(null, "", "/other-tenant/jobs/4723040005");
      if (mutation === "login") { const node = document.createElement("input"); node.type = "password"; form.append(node); }
      if (mutation === "modal") { const node = document.createElement("div"); node.setAttribute("role", "dialog"); node.textContent = "Human checkpoint"; form.append(node); }
    }, mutation);
    assert.equal((await f.fill()).outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});

test("approved value, expected-before, document, digest, action bounds and deadline cannot be substituted", async () => {
  const f = await fixture(); try {
    for (const extra of [
      { value: "Unreviewed value" }, { expected_before: "Unreviewed before" }, { document_id: "wrong-document" }, { frame_id: 1 },
      { context_digest: "0".repeat(64) }, { deadline: Date.now() - 1 }, { deadline: Date.now() + 60_000 }, { cancelled: true },
      { review_digest: "0".repeat(64) }, { reviewed_answers_sha256: "0".repeat(64) },
      { field_id: "question_8970458005", value: "Guessed sensitive answer" }, { field_id: "submit-application", value: "submit" },
    ]) { assert.equal((await f.fill("first_name", extra)).outcome, "blocked"); await f.noWrite(); }
    const changedSubset = await f.fill("first_name", { value: "Changed approved subset" }, { ...f.reviewed, reviewed_answers: { ...answers, first_name: "Changed approved subset" } });
    assert.equal(changedSubset.outcome, "blocked"); assert.match(changedSubset.reason, /subset changed/); await f.noWrite();
    await f.page.locator("#first_name").evaluate((node) => { node.value = "Candidate existing value"; });
    assert.equal((await f.fill()).outcome, "blocked"); assert.equal(await f.page.locator("#first_name").inputValue(), "Candidate existing value"); await f.noWrite();
  } finally { await f.close(); }
});

test("manual attachment blocks assistance without reading/uploading bytes or claiming completion", async () => {
  const f = await fixture(); try {
    await f.page.locator("#resume").setInputFiles({ name: "synthetic.pdf", mimeType: "application/pdf", buffer: Buffer.from("Local synthetic bytes") });
    const result = await f.fill(); assert.equal(result.outcome, "blocked"); assert.match(result.reason, /attachment is unverified/);
    assert.deepEqual(await f.page.evaluate(() => window.observed), { input: ["resume"], change: ["resume"], blur: [], submit: 0, file: 1 });
  } finally { await f.close(); }
});

test("capture overflow fails closed rather than fingerprinting a truncated manual context", async () => {
  const f = await fixture(); try {
    await f.page.locator("#company-name-0").evaluate((node) => { node.value = "x".repeat(32_769); });
    assert.equal((await f.fill()).outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});

test("native input value setter bypasses a controlled-input own setter without focus or submit", async () => {
  const f = await fixture(); try {
    await f.worker.evaluate(async (tab) => {
      await chrome.scripting.executeScript({ target: { tabId: tab, frameIds: [0] }, func: () => {
        const node = document.getElementById("first_name"); const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value"); globalThis.ownSetterCalls = 0;
        Object.defineProperty(node, "value", { get() { return descriptor.get.call(this); }, set() { globalThis.ownSetterCalls += 1; throw new Error("Own setter must not be used"); } });
      } });
    }, f.tab);
    assert.equal((await f.fill()).outcome, "filled_only");
    assert.equal(await f.worker.evaluate(async (tab) => (await chrome.scripting.executeScript({ target: { tabId: tab, frameIds: [0] }, func: () => globalThis.ownSetterCalls }))[0].result, f.tab), 0);
    assert.deepEqual(await f.page.evaluate(() => window.observed), { input: ["first_name"], change: ["first_name"], blur: [], submit: 0, file: 0 });
  } finally { await f.close(); }
});

test("manual change during answer digest yields blocks before the first event", async () => {
  const f = await fixture(); try {
    await f.worker.evaluate(async (tab) => {
      await chrome.scripting.executeScript({ target: { tabId: tab, frameIds: [0] }, func: () => {
        const digest = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = async (...args) => { const bytes = await digest(...args); if (new TextDecoder().decode(args[1]).includes('"reviewed_answers":')) document.getElementById("company-name-0").value = "Changed during review digest"; return bytes; };
      } });
    }, f.tab);
    const result = await f.fill(); assert.equal(result.outcome, "blocked"); assert.match(result.reason, /context changed while checking/); await f.noWrite();
  } finally { await f.close(); }
});

test("another assisted field changing during answer digest blocks before the target event", async () => {
  const f = await fixture(); try {
    await f.worker.evaluate(async (tab) => {
      await chrome.scripting.executeScript({ target: { tabId: tab, frameIds: [0] }, func: () => {
        const digest = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = async (...args) => { const bytes = await digest(...args); if (new TextDecoder().decode(args[1]).includes('"reviewed_answers":')) document.getElementById("last_name").value = "Changed during field lease"; return bytes; };
      } });
    }, f.tab);
    const result = await f.fill(); assert.equal(result.outcome, "blocked"); assert.match(result.reason, /Assisted values changed/); await f.noWrite();
  } finally { await f.close(); }
});

test("the complete exact current assisted-values map is required at mutation time", async () => {
  const f = await fixture(); try {
    for (const current_assisted_values of [{}, { first_name: "" }, { ...f.review.assisted_values, foreign: "" }, { ...f.review.assisted_values, last_name: "Changed expectation" }]) {
      assert.equal((await f.fill("first_name", {}, { ...f.reviewed, current_assisted_values })).outcome, "blocked"); await f.noWrite();
    }
  } finally { await f.close(); }
});

test("manual candidate state is hashed locally and exact returned context hashes round trip", async () => {
  const f = await fixture(); try {
    await f.page.evaluate(() => {
      document.getElementById("company-name-0").value = "Synthetic private employer history";
      const gender = document.getElementById("question_8970458005"); gender.value = "Synthetic private choice"; gender.dataset.selectedValue = "Synthetic private selection";
      gender.setAttribute("value", "Synthetic private default selection");
      const summary = document.createElement("p"); summary.textContent = "Synthetic private visible manual answer"; gender.form.append(summary);
    });
    const inspection = (await f.invoke()).result; assert.equal(inspection.outcome, "inspected");
    const returned = JSON.stringify(inspection);
    for (const secret of ["Synthetic private employer history", "Synthetic private choice", "Synthetic private selection", "Synthetic private default selection", "Synthetic private visible manual answer"]) assert.equal(returned.includes(secret), false);
    assert.match(inspection.manual_fields.find((field) => field.id === "company-name-0").manual_value_sha256, /^[a-f0-9]{64}$/);
    assert.equal(hash(canonical(inspection.context)), inspection.context_digest);
    await f.noWrite();
  } finally { await f.close(); }
});

test("child frame is rejected even when caller supplies a claimed top-frame identity", async () => {
  const f = await fixture(); try {
    await f.page.evaluate(() => { const node = document.createElement("iframe"); node.srcdoc = "<p>Local frame</p>"; document.body.append(node); });
    const source = await import("../extension/razorpay-adapter.js").then(({ razorpayAdapter }) => razorpayAdapter.toString());
    const frame = f.page.frames().find((entry) => entry.parentFrame() === f.page.mainFrame());
    const result = await frame.evaluate(async ({ source, document_id }) => (0, eval)(`(${source})`)(null, null, { frame_id: 0, document_id }), { source, document_id: f.review.document_id });
    assert.equal(result.outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});
