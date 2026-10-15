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

const ROOT = new URL("../../", import.meta.url);
const WEBSITE = "https://www.hirewizhq.com";
const PORTAL = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005";
const EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb";
const message = (popup, value) => popup.evaluate((input) => chrome.runtime.sendMessage(input), value);
async function fixture({ granted = true, loseBegin = false } = {}) {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-reviewed-assistance-`);
  const pending = []; const requests = []; let errors = ""; let context;
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
        const html = fixtureHtml().replace(/<main[^>]*>/, "<main>").replace(/<script type="module">[\s\S]*?<\/script>/,
          `<script>window.events={input:[],change:[],blur:[],submit:0};document.querySelector('form').addEventListener('input',e=>events.input.push(e.target.id));document.querySelector('form').addEventListener('change',e=>events.change.push(e.target.id));document.querySelector('#email').addEventListener('blur',()=>events.blur.push('email'));document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();events.submit++});</script>`)
          .replace('action="/fixture/razorpay-like/apply"', `action="${PORTAL}"`);
        return route.fulfill({ status: 200, contentType: "text/html", body: html });
      }
      if (url.startsWith(`${WEBSITE}/api/browser-assistance/device/`) && request.method() === "POST") {
        const origin = request.headers().origin;
        const result = await bridge({ operation: "device", origin: origin || "", body: request.postDataJSON() });
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
    return { popup, portal, worker, context, bridge, requests, connect, prepare, approve,
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
