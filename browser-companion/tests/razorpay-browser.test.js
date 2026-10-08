// Run explicitly. Not added to Node unit or existing MV3 browser scripts.
import assert from "node:assert/strict";
import test from "node:test";
import { mkdir } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";
import { startRazorpayFixture } from "../fixtures/razorpay/server.js";
import { CHALLENGES, ASSISTED_FIELDS } from "../fixtures/razorpay/schema.js";

const answers = Object.freeze({ first_name: "Synthetic Alice", last_name: "Fixture", email: "synthetic@example.test", question_8970454005: "https://example.test/synthetic-profile", question_8970455005: "https://example.test/synthetic-site", question_8970456005: "10", question_8970461005: "Synthetic Bengaluru" });

async function fixture() {
  const server = await startRazorpayFixture(); let browser;
  const foreign = []; const errors = [];
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ serviceWorkers: "block" });
    await context.route("**/*", (route) => {
      if (new URL(route.request().url()).origin !== server.origin) { foreign.push(route.request().url()); return route.abort(); }
      return route.continue();
    });
    const page = await context.newPage(); page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${server.origin}/fixture/razorpay-like/apply`);
    await page.waitForFunction(() => Boolean(window.fixtureControls));
    await page.evaluate(async (origin) => { window.scaffold = await import("/fixture/razorpay-like/adapter.js"); window.review = window.scaffold.inspectSyntheticForm(origin); }, server.origin);
    const fill = async (field = "first_name", options = {}) => page.evaluate(({ origin, values, field, options }) => {
      const inspection = window.review;
      const gate = window.scaffold.issueSyntheticGate({ inspection, answers: values, field_id: field, expected_before: options.before ?? "", deadline: Date.now() + 1500, acknowledged: true });
      return window.scaffold.fillSyntheticField({ expectedOrigin: origin, inspection, answers: values, gate, cancelled: options.cancelled });
    }, { origin: server.origin, values: answers, field, options });
    const noWrite = async () => { assert.deepEqual(await page.evaluate(() => window.fixtureEvents), { input: [], change: [], email: 0, file: 0, submit: 0 }); assert.equal(server.events.length, 0); };
    return { ...server, page, foreign, errors, fill, noWrite,
      async close() { await browser.close(); await server.close(); assert.deepEqual(foreign, [], "No request may leave the exact loopback fixture origin"); assert.deepEqual(errors, [], "No unhandled fixture errors"); } };
  } catch (error) { await browser?.close(); await server.close(); throw error; }
}

test("inspection is read-only, shows full-form handoff and seven optional assisted fields", async () => {
  const f = await fixture(); try {
    const review = await f.page.evaluate(() => window.review);
    assert.equal(review.outcome, "inspected"); assert.equal(review.assisted_fields.length, 7); assert.ok(review.required_controls > 8);
    assert.equal(review.status, "manual_handoff"); assert.match(review.message, /no receipt or charge/);
    assert.ok(review.manual_fields.some((field) => field.id === "company-name-0" && field.required));
    assert.ok(review.manual_fields.some((field) => field.id === "country" && field.role === "combobox"));
    assert.ok(review.manual_fields.some((field) => field.id === "phone" && field.type === "tel"));
    assert.ok(review.manual_fields.some((field) => field.id === "question_8970458005" && field.required));
    await f.noWrite();
    const directory = process.env.RAZORPAY_FIXTURE_SCREENSHOT_DIR;
    if (directory) { await mkdir(directory, { recursive: true }); for (const width of [1280, 390]) { await f.page.setViewportSize({ width, height: 900 }); assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await f.page.screenshot({ path: `${directory}/synthetic-form-${width}.png`, fullPage: true }); } }
  } finally { await f.close(); }
});

test("seven exact values cause only reviewed text events and email blur; never upload/submit", async () => {
  const f = await fixture(); try {
    for (const [id] of ASSISTED_FIELDS) assert.equal((await f.fill(id)).outcome, "filled_only");
    await f.page.evaluate(() => window.fixtureFlush());
    const events = await f.page.evaluate(() => window.fixtureEvents);
    assert.equal(events.input.length, 7); assert.equal(events.change.length, 7); assert.equal(events.email, 1); assert.equal(events.file, 0); assert.equal(events.submit, 0);
    assert.deepEqual(f.events, [{ kind: "email_validation", address: answers.email }]);
    assert.deepEqual(await f.page.evaluate(() => window.fixtureState.answers), answers);
    await expect(f.page.locator("#question_8970458005")).toHaveValue("");
    await expect(f.page.locator("#country")).toHaveValue("");
    assert.equal(await f.page.locator("#resume").evaluate((node) => node.files.length), 0);
  } finally { await f.close(); }
});

test("absent acknowledgement, reconstructed gate, replay, expiry and cancellation never authorize writes", async () => {
  const f = await fixture(); try {
    const result = await f.page.evaluate(async ({ origin, values }) => {
      const a = window.scaffold; const review = window.review;
      const request = { inspection: review, answers: values, field_id: "first_name", expected_before: "", deadline: Date.now() + 1000, acknowledged: false };
      let rejected = false; try { a.issueSyntheticGate(request); } catch { rejected = true; }
      request.acknowledged = true; const gate = a.issueSyntheticGate(request);
      const clone = a.fillSyntheticField({ expectedOrigin: origin, inspection: review, answers: values, gate: { ...gate } });
      const cancel = a.fillSyntheticField({ expectedOrigin: origin, inspection: review, answers: values, gate, cancelled: true });
      const replay = a.fillSyntheticField({ expectedOrigin: origin, inspection: review, answers: values, gate });
      const expiring = a.issueSyntheticGate({ ...request, deadline: Date.now() + 50 }); await new Promise((resolve) => setTimeout(resolve, 80));
      const expired = a.fillSyntheticField({ expectedOrigin: origin, inspection: review, answers: values, gate: expiring });
      return { rejected, outcomes: [clone, cancel, replay, expired].map((x) => x.outcome) };
    }, { origin: f.origin, values: answers });
    assert.equal(result.rejected, true); assert.deepEqual(result.outcomes, ["blocked", "blocked", "blocked", "blocked"]); await f.noWrite();
  } finally { await f.close(); }
});

test("known real origin/tenant arguments are rejected without navigating or issuing real requests", async () => {
  const f = await fixture(); try {
    const wrongOrigin = await f.page.evaluate(() => window.scaffold.inspectSyntheticForm("https://job-boards.greenhouse.io"));
    assert.equal(wrongOrigin.outcome, "blocked");
    await f.page.locator("main").evaluate((node) => { node.dataset.tenant = "razorpaysoftwareprivatelimited"; });
    assert.equal((await f.fill()).outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});

for (const challenge of CHALLENGES) test(`${challenge} checkpoint pauses without credentials or field mutation`, async () => {
  const f = await fixture(); try {
    await f.page.evaluate((value) => window.fixtureControls.checkpoint(value), challenge);
    const result = await f.fill(); assert.equal(result.outcome, "blocked"); assert.match(result.reason, /Human checkpoint/); await f.noWrite();
  } finally { await f.close(); }
});

for (const change of ["conditional", "manual-value", "recipient", "form-version", "duplicate", "disabled", "readonly", "hidden", "css-parent", "transparent-parent", "label", "type", "path"]) test(`${change} after review invalidates step before disclosure`, async () => {
  const f = await fixture(); try {
    await f.page.evaluate((change) => {
      const node = document.querySelector("#first_name"); const root = document.querySelector("main");
      if (change === "conditional") window.fixtureControls.conditional();
      if (change === "manual-value") document.querySelector("#company-name-0").value = "Manual edit after review";
      if (change === "recipient") root.dataset.recipientPolicy = JSON.stringify({ version: "unreviewed" });
      if (change === "form-version") root.dataset.formVersion = "changed";
      if (change === "duplicate") node.after(node.cloneNode());
      if (change === "disabled") node.disabled = true;
      if (change === "readonly") node.readOnly = true;
      if (change === "hidden") node.hidden = true;
      if (change === "css-parent" || change === "transparent-parent") { const wrapper = document.createElement("div"); node.before(wrapper); wrapper.append(node); wrapper.style[change === "css-parent" ? "display" : "opacity"] = change === "css-parent" ? "none" : "0"; }
      if (change === "label") node.labels[0].textContent = "Changed meaning";
      if (change === "type") node.type = "password";
      if (change === "path") history.replaceState(null, "", "/fixture/foreign-tenant/apply");
    }, change);
    assert.equal((await f.fill()).outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});

test("candidate prefilled assisted value is never overwritten", async () => {
  const f = await fixture(); try {
    await f.page.locator("#first_name").evaluate((node) => { node.value = "Candidate existing value"; });
    assert.equal((await f.fill()).outcome, "blocked"); await expect(f.page.locator("#first_name")).toHaveValue("Candidate existing value"); await f.noWrite();
  } finally { await f.close(); }
});

test("manual conditional employment/combobox/file paths are explicit and invalidate old review", async () => {
  const f = await fixture(); try {
    await f.page.locator("#country").click(); await f.page.getByRole("option", { name: "India (synthetic subset)", exact: true }).click();
    await f.page.locator("#current-role-0_1").check();
    await expect(f.page.locator("#end-date-month-0")).toBeDisabled(); await expect(f.page.locator("#end-date-year-0")).toBeDisabled();
    await f.page.getByRole("button", { name: "Add another employment — manual", exact: true }).click();
    await expect(f.page.locator("#company-name-1")).toBeVisible(); await expect(f.page.getByText("Employment 2 — manual", { exact: true })).toBeVisible();
    await f.page.getByRole("button", { name: "Enter manually", exact: true }).click(); await expect(f.page.locator("#resume_text")).toBeVisible();
    assert.equal((await f.fill()).outcome, "blocked");
    await f.page.locator("#resume").setInputFiles({ name: "synthetic-review.pdf", mimeType: "application/pdf", buffer: Buffer.from("Synthetic local file, not a rendered resume") });
    await f.page.evaluate(() => window.fixtureFlush());
    assert.equal(f.events.filter((x) => x.kind === "manual_file").length, 1);
    assert.equal(f.events.filter((x) => x.kind === "manual_submit").length, 0);
    assert.equal(await f.page.locator("#question_8970458005").inputValue(), "");
  } finally { await f.close(); }
});

test("cancellation cannot recall earlier email callback and completion never claims applied or charge", async () => {
  const f = await fixture(); try {
    const result = await f.fill("email"); await f.page.evaluate(() => window.fixtureFlush());
    assert.equal(result.outcome, "filled_only"); assert.match(result.message, /no application, receipt or charge/);
    assert.equal((await f.fill("last_name", { cancelled: true })).outcome, "blocked");
    assert.equal(f.events.filter((x) => x.kind === "email_validation").length, 1);
    assert.equal(f.events.filter((x) => x.kind === "manual_submit").length, 0);
    await expect(f.page.locator("#last_name")).toHaveValue("");
  } finally { await f.close(); }
});

test("preselected/replaced manual files always block; metadata is never treated as byte proof", async () => {
  const f = await fixture(); try {
    for (const text of ["First synthetic bytes", "Other synthetic bytes"]) {
      await f.page.locator("#resume").setInputFiles({ name: "same-name.pdf", mimeType: "application/pdf", buffer: Buffer.from(text) });
      await f.page.evaluate(() => window.fixtureFlush());
      const review = await f.page.evaluate((origin) => window.scaffold.inspectSyntheticForm(origin), f.origin);
      assert.equal(review.outcome, "blocked"); assert.match(review.reason, /file is unverified/);
      assert.equal((await f.fill()).outcome, "blocked");
    }
    assert.equal(f.events.filter((x) => x.kind === "manual_file").length, 2);
    assert.equal(f.events.filter((x) => x.kind === "email_validation" || x.kind === "manual_submit").length, 0);
  } finally { await f.close(); }
});

test("failed callback is recorded without unhandled rejection, retries or erasing disclosure", async () => {
  const f = await fixture(); try {
    f.failures.email = true; assert.equal((await f.fill("email")).outcome, "filled_only");
    await f.page.evaluate(() => window.fixtureFlush());
    assert.deepEqual(await f.page.evaluate(() => window.fixtureCallbackErrors), ["Local recorder rejected request"]);
    assert.equal(f.events.filter((x) => x.kind === "email_validation").length, 1);
    assert.equal((await f.fill("first_name", { cancelled: true })).outcome, "blocked");
  } finally { await f.close(); }
});

test("a supported ID outside the reviewed form cannot obtain a synthetic gate", async () => {
  const f = await fixture(); try {
    await f.page.locator("#first_name").evaluate((node) => document.body.append(node));
    const result = await f.page.evaluate(({ origin, values }) => {
      const review = window.scaffold.inspectSyntheticForm(origin);
      try { window.scaffold.issueSyntheticGate({ inspection: review, answers: values, field_id: "first_name", expected_before: "", deadline: Date.now() + 1000, acknowledged: true }); return false; } catch { return true; }
    }, { origin: f.origin, values: answers });
    assert.equal(result, true); await f.noWrite();
  } finally { await f.close(); }
});

test("child frame cannot inspect or mutate the top-level fixture", async () => {
  const f = await fixture(); try {
    await f.page.evaluate(() => document.body.append(document.createElement("iframe")));
    const frame = f.page.frames().find((frame) => frame.parentFrame() === f.page.mainFrame());
    const result = await frame.evaluate(async (origin) => {
      const module = await import(origin + "/fixture/razorpay-like/adapter.js");
      return module.inspectSyntheticForm(origin);
    }, f.origin);
    assert.equal(result.outcome, "blocked"); await f.noWrite();
  } finally { await f.close(); }
});

test("one-field assistance explicitly hands every other missing required control back to the candidate", async () => {
  const f = await fixture(); try {
    const result = await f.page.evaluate((origin) => {
      const values = { first_name: "Synthetic Alice" }; const a = window.scaffold;
      const gate = a.issueSyntheticGate({ inspection: window.review, answers: values, field_id: "first_name", expected_before: "", deadline: Date.now() + 1000, acknowledged: true });
      return a.fillSyntheticField({ expectedOrigin: origin, inspection: window.review, answers: values, gate });
    }, f.origin);
    assert.equal(result.outcome, "filled_only");
    assert.ok(result.manual_handoff.some((x) => x.id === "last_name"));
    assert.ok(result.manual_handoff.some((x) => x.id === "email"));
    assert.ok(result.manual_handoff.some((x) => x.id === "resume"));
    assert.ok(result.manual_handoff.some((x) => x.id === "company-name-0"));
    assert.equal(result.manual_handoff.some((x) => x.id === "first_name"), false);
  } finally { await f.close(); }
});

test("seven approved answers do not hide unfilled required controls after one-field assistance", async () => {
  const f = await fixture(); try {
    const result = await f.fill("first_name");
    assert.equal(result.outcome, "filled_only");
    const expected = await f.page.evaluate(() => window.review.context.fields.filter((field) => field.required && field.id !== "first_name").map(({ id }) => id));
    assert.ok(expected.length > 8);
    assert.deepEqual(result.manual_handoff.map(({ id }) => id), expected);
    for (const [id] of ASSISTED_FIELDS.slice(1)) {
      await expect(f.page.locator(`#${id}`)).toHaveValue("");
      assert.ok(result.manual_handoff.some((field) => field.id === id));
    }
    await expect(f.page.locator("#first_name")).toHaveValue(answers.first_name);
    assert.equal(result.manual_handoff.some((field) => field.id === "first_name"), false);
    assert.deepEqual(await f.page.evaluate(() => window.fixtureEvents), { input: ["first_name"], change: ["first_name"], email: 0, file: 0, submit: 0 });
    assert.deepEqual(f.events, []);
  } finally { await f.close(); }
});

test("handoff keeps mismatched assisted values and excludes only exact approved current values", async () => {
  const f = await fixture(); try {
    await f.page.evaluate((values) => {
      document.querySelector("#last_name").value = "Synthetic unmatched surname";
      document.querySelector("#email").value = values.email;
    }, answers);
    const result = await f.fill("first_name");
    assert.equal(result.outcome, "filled_only");
    const expected = await f.page.evaluate(() => window.review.context.fields.filter((field) => field.required && !["first_name", "email"].includes(field.id)).map(({ id }) => id));
    assert.deepEqual(result.manual_handoff.map(({ id }) => id), expected);
    assert.ok(result.manual_handoff.some((field) => field.id === "last_name"));
    assert.equal(result.manual_handoff.some((field) => field.id === "email"), false);
    await expect(f.page.locator("#last_name")).toHaveValue("Synthetic unmatched surname");
    await expect(f.page.locator("#email")).toHaveValue(answers.email);
    assert.deepEqual(await f.page.evaluate(() => window.fixtureEvents), { input: ["first_name"], change: ["first_name"], email: 0, file: 0, submit: 0 });
    assert.deepEqual(f.events, []);
  } finally { await f.close(); }
});
