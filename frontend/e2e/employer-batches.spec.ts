import { mkdir } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { encode } from "next-auth/jwt";

import { batchApplication, batchCatalog, quotedBatch } from "../lib/fixtures/employerBatch";
import type { EmployerApplicationBatch } from "../lib/employerJobs";

const base = "/v1/employer-jobs";
const errors = new WeakMap<Page, string[]>();
function fixture() {
  const applications = [batchApplication(1), batchApplication(2)];
  const catalog = structuredClone(batchCatalog);
  let batch: EmployerApplicationBatch | null = null;
  let owner = "a";
  const state = { interruptQuote: false, refuseApprove: false };
  const requests: { path: string; method: string; body: Record<string, unknown>; owner: string }[] = [];
  const unexpected: string[] = [];
  async function install(context: BrowserContext, baseURL: string) {
    const token = await encode({ secret: process.env.PLAYWRIGHT_AUTH_SECRET || "playwright-secret-at-least-thirty-two-characters", token: { sub: "424242", email: "fixture@example.com", hirewizUserId: 424242, accessToken: "local-fixture-only" }, maxAge: 3600 });
    await context.addCookies([{ name: "next-auth.session-token", value: token, url: baseURL, httpOnly: true, sameSite: "Lax" }]);
    await context.addInitScript(() => { localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "essential", savedAt: new Date().toISOString() })); });
    await context.route("**/*", async (route) => {
      if (new URL(route.request().url()).origin !== new URL(baseURL).origin) { await route.abort(); return; }
      if (!["GET", "HEAD", "OPTIONS"].includes(route.request().method())) { unexpected.push(`Non-fixture mutation ${route.request().url()}`); await route.abort(); return; }
      await route.continue();
    });
    await context.route("**/api/auth/session", async (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ user: { id: owner === "a" ? "424242" : "424243", email: `${owner}@fixture.example`, name: "Taylor Fixture" }, expires: "2099-01-01T00:00:00Z" }) }));
    await context.route("**/api/backend/**", async (route) => {
      const request = route.request(), path = new URL(request.url()).pathname.replace("/api/backend", ""), method = request.method();
      const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : {};
      requests.push({ path, method, body, owner });
      let value: unknown, status = 200;
      if (owner === "b" && (path.includes("/applications/") || path.includes("/application-batches/"))) { status = 404; value = { detail: "Not found" }; }
      else if (path === "/auth/profile") value = { email: "fixture@example.com", tier: "free", ai_credits: 40, job_service_credits: catalog.balance, full_name: "Taylor Fixture" };
      else if (path === "/v1/features") value = { features: { career_workspace: { enabled: true } } };
      else if (path === "/resume/list") value = { resumes: [] };
      else if (path === `${base}/catalog`) value = catalog;
      else if (path === `${base}/searches`) value = { items: [] };
      else if (path === `${base}/applications`) value = { items: owner === "a" ? applications : [] };
      else if (path === `${base}/application-batches` && method === "POST") {
        if (!batch) batch = quotedBatch(applications.filter((app) => (body.items as { application_id: string }[]).some((item) => item.application_id === app.id)), Number(body.max_total_credits));
        if (state.interruptQuote) { state.interruptQuote = false; status = 503; value = { detail: "Quote response interrupted; use the same intent." }; } else value = batch;
      } else if (path.startsWith(`${base}/application-batches/`) && batch) {
        if (method === "POST" && path.endsWith("/approve")) {
          if (state.refuseApprove) { status = 409; value = { detail: "A current employer policy changed; review a new package." }; }
          else { batch.status = "approved"; batch.approved_at = new Date().toISOString(); applications.forEach((app) => { app.batch_id = batch!.id; app.status = "approved"; }); }
        }
        if (method === "POST" && path.endsWith("/execute")) { batch.status = "queued"; applications.forEach((app) => { app.status = "queued"; app.reserved_credits = app.credit_cost; }); }
        if (method === "POST" && path.endsWith("/cancel")) { batch.status = "cancelled"; batch.cancelled_at = new Date().toISOString(); applications.forEach((app) => { if (app.status === "unknown" || app.status === "submitting") app.cancel_requested = true; else if (app.status !== "confirmed") { app.status = "cancelled"; app.reserved_credits = 0; } }); }
        batch.application_statuses = Object.fromEntries(applications.map((app) => [app.id, app.status]));
        value ??= batch;
      } else if (path.startsWith(`${base}/applications/`)) {
        const app = applications.find((app) => path.includes(`/${app.id}`));
        if (app && path.endsWith("/artifact")) { await route.fulfill({ contentType: app.artifact!.media_type, body: Buffer.from("Synthetic sealed DOCX review fixture") }); return; }
        if (app && method === "GET") value = app;
      }
      if (value === undefined) { unexpected.push(`${method} ${path}`); status = 503; value = { detail: "Unexpected local fixture request" }; }
      await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    });
  }
  return { applications, catalog, state, requests, unexpected, install, get batch() { return batch; }, set batch(value: EmployerApplicationBatch | null) { batch = value; }, switchOwner() { owner = "b"; } };
}
async function selectBatch(page: Page) {
  await page.goto("/employer-jobs");
  await page.getByRole("checkbox", { name: /^Select Backend Engineer/ }).check();
  await page.getByRole("checkbox", { name: /^Select Infrastructure Engineer/ }).check();
  await page.getByRole("button", { name: "Review selected batch (2)" }).click();
  await expect(page.getByRole("dialog").getByLabel("Maximum service credit budget")).toHaveValue("18");
}
async function reviewEveryJob(page: Page) {
  const dialog = page.getByRole("dialog");
  for (const number of [1, 2]) {
    const checked = dialog.getByRole("checkbox", { name: new RegExp(`^I reviewed job ${number},`) });
    await expect(checked).toBeDisabled();
    const download = page.waitForEvent("download");
    await dialog.getByRole("button", { name: `Download exact file for job ${number}`, exact: true }).click();
    await download;
    await checked.check();
  }
}
async function layout(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(result.violations).toEqual([]);
}
async function screenshots(page: Page, project: string, state: string) {
  if (!process.env.PLAYWRIGHT_SCREENSHOT_DIR) return;
  await mkdir(process.env.PLAYWRIGHT_SCREENSHOT_DIR, { recursive: true });
  const body = page.getByRole("dialog").locator(".overflow-y-auto");
  const dimensions = await body.evaluate((element) => ({ height: element.clientHeight, total: element.scrollHeight }));
  for (let offset = 0, index = 0; offset < dimensions.total; offset += dimensions.height, index++) {
    await body.evaluate((element, top) => element.scrollTo({ top, behavior: "instant" }), offset);
    await page.screenshot({ path: `${process.env.PLAYWRIGHT_SCREENSHOT_DIR}/batch-${state}-${project}-${index}.png` });
  }
  // Also retain a readable full-content export. Responsive behavior is checked
  // above at the real viewport; only this export expands the scroll container.
  const dialog = page.getByRole("dialog");
  const dialogStyle = await dialog.getAttribute("style"), bodyStyle = await body.getAttribute("style");
  try {
    await body.evaluate((element) => { element.scrollTop = 0; (element as HTMLElement).style.overflow = "visible"; });
    await dialog.evaluate((element) => { (element as HTMLElement).style.position = "absolute"; (element as HTMLElement).style.maxHeight = "none"; });
    await page.screenshot({ path: `${process.env.PLAYWRIGHT_SCREENSHOT_DIR}/batch-${state}-${project}-expanded-full.png`, fullPage: true });
  } finally {
    await dialog.evaluate((element, style) => { if (style === null) element.removeAttribute("style"); else element.setAttribute("style", style); }, dialogStyle);
    await body.evaluate((element, style) => { if (style === null) element.removeAttribute("style"); else element.setAttribute("style", style); }, bodyStyle);
  }
}
test.beforeEach(({ page }) => {
  const messages: string[] = [];
  errors.set(page, messages);
  page.on("pageerror", (error) => messages.push(error.message));
  page.on("console", (message) => { if (message.type() === "error" && !/Failed to load resource: the server responded with a status of (409|503)/.test(message.text())) messages.push(message.text()); });
});
test.afterEach(({ page }) => expect(errors.get(page)).toEqual([]));

test("exact jobs, files, actions, fixed prices and budget require review before separate approval and queueing", async ({ page, context, baseURL }, info) => {
  const f = fixture(); await f.install(context, baseURL!); await page.emulateMedia({ reducedMotion: "reduce" }); await selectBatch(page);
  const dialog = page.getByRole("dialog"); await dialog.getByLabel("Maximum service credit budget").fill("17"); await expect(dialog.getByRole("button", { name: "Create exact batch quote" })).toBeDisabled();
  await dialog.getByLabel("Maximum service credit budget").fill("20"); await dialog.getByRole("button", { name: "Create exact batch quote" }).click();
  await expect(dialog.getByRole("heading", { name: "2 exact jobs · 18 quoted service credits" })).toBeVisible();
  await expect(dialog.getByText("Maximum authorized budget: 20 service credits.", { exact: false })).toBeVisible();
  await expect(dialog.getByText("7 service credits per confirmed complete application.", { exact: true })).toBeVisible(); await expect(dialog.getByText("11 service credits per confirmed complete application.", { exact: true })).toBeVisible();
  await dialog.locator("summary").filter({ hasText: /^Application limits$/ }).click(); await expect(dialog.getByText(/^Candidate limits:/)).toBeVisible(); await dialog.locator("summary").filter({ hasText: /^Application limits$/ }).click();
  for (const number of [1, 2]) { const job = dialog.getByRole("article", { name: `Batch job ${number}` }); await expect(job.getByText(`Price version: fixture-price-${number}`, { exact: true })).not.toBeVisible(); await job.locator("summary").filter({ hasText: /^Package details$/ }).click(); await expect(job.getByText(`Price version: fixture-price-${number}`, { exact: true })).toBeVisible(); await job.locator("summary").filter({ hasText: /^Package details$/ }).click(); }
  await expect(dialog.getByText("Original file", { exact: false })).toBeVisible(); await expect(dialog.getByText("Custom file", { exact: false })).toBeVisible();
  await expect(dialog.getByText("Tenant routing identifier (employer metadata)", { exact: false })).toHaveCount(2); await expect(dialog.getByText("Future role contact: Not agreed (optional)", { exact: true })).toHaveCount(2);
  await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeDisabled();
  await reviewEveryJob(page); await layout(page);
  await screenshots(page, info.project.name, "review");
  await dialog.getByRole("button", { name: "Approve these 2 exact applications" }).focus(); await page.keyboard.press("Enter"); await expect(dialog.getByRole("heading", { name: "Approved; not queued" })).toBeVisible();
  expect(f.requests.filter((request) => request.path.endsWith("/execute"))).toHaveLength(0);
  await dialog.getByRole("button", { name: "Close batch review" }).click();
  await page.getByRole("button", { name: /Backend Engineer Example Studio Approved/ }).click();
  await expect(dialog.getByRole("link", { name: "Review this application's batch" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Approve and submit" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close application review" }).click();
  await page.getByRole("button", { name: "View this application's batch" }).first().click();
  await expect(dialog.getByRole("heading", { name: "Approved; not queued" })).toBeVisible();
  await dialog.getByRole("button", { name: "Queue these approved applications" }).click(); await expect(dialog.getByRole("status")).toContainText("Batch accepted into the queue");
  await expect(dialog.getByText("7 service credits reserved. Waiting for an employer-confirmed outcome.", { exact: true })).toBeVisible();
  await expect(dialog.getByText("11 service credits reserved. Waiting for an employer-confirmed outcome.", { exact: true })).toBeVisible();
  const before = f.requests.filter((request) => request.method === "GET" && /^\/v1\/employer-jobs\/applications\//.test(request.path)).length;
  await expect.poll(() => f.requests.filter((request) => request.path === `${base}/application-batches/batch_local_fixture` && request.method === "GET").length).toBeGreaterThan(1);
  expect(f.requests.filter((request) => request.method === "GET" && /^\/v1\/employer-jobs\/applications\//.test(request.path)).length).toBe(before);
  f.applications[0].status = "confirmed"; f.applications[0].charged_credits = 7; f.applications[0].reserved_credits = 0; f.applications[1].status = "unknown";
  await expect(dialog.getByText("Submission confirmed", { exact: true })).toBeVisible(); await expect(dialog.getByText("Outcome unknown", { exact: true })).toBeVisible(); await expect(dialog.getByText(/No automatic repeat send/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel remaining batch work" }).click(); await expect(dialog.getByRole("status")).toContainText("Cancellation requested for remaining work"); await expect(dialog.getByText("Submission confirmed", { exact: true })).toBeVisible(); await expect(dialog.getByText("Outcome unknown", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Cancellation requested. This does not recall an in-flight send.", { exact: true })).toBeVisible(); expect(f.applications[1].reserved_credits).toBe(11); await layout(page);
  const terminalReads = f.requests.filter((request) => request.method === "GET" && request.path === `${base}/application-batches/batch_local_fixture`).length;
  await page.waitForTimeout(2200);
  expect(f.requests.filter((request) => request.method === "GET" && request.path === `${base}/application-batches/batch_local_fixture`).length).toBe(terminalReads);
  await screenshots(page, info.project.name, "status");
  expect(f.requests.filter((request) => request.path.endsWith("/execute"))).toHaveLength(1); expect(f.requests.filter((request) => request.path.endsWith("/approve"))).toHaveLength(1);
  const quoted = f.requests.find((request) => request.path === `${base}/application-batches`)!; expect(quoted.body.max_total_credits).toBe(20); expect(quoted.body.items).toEqual(f.applications.map((app) => ({ application_id: app.id, package_digest: app.package_digest, allowed_actions: ["fill", "upload", "submit"] }))); expect(f.unexpected).toEqual([]);
});

test("interrupted quote retries reuse one immutable intent without approving or queueing", async ({ page, context, baseURL }) => {
  const f = fixture(); f.state.interruptQuote = true; await f.install(context, baseURL!); await selectBatch(page); const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Create exact batch quote" }).click(); await expect(dialog.getByRole("alert").filter({ hasText: "Quote response interrupted" })).toBeVisible();
  await dialog.getByRole("button", { name: "Create exact batch quote" }).click(); await expect(dialog.getByRole("heading", { name: /2 exact jobs/ })).toBeVisible();
  const intents = f.requests.filter((request) => request.path === `${base}/application-batches`);
  expect(intents).toHaveLength(2); expect(intents[0].body).toEqual(intents[1].body); expect(f.requests.some((request) => /\/(approve|execute)$/.test(request.path))).toBe(false); expect(f.unexpected).toEqual([]); await layout(page);
});

test("changed packages cannot reuse an earlier exact batch review", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await selectBatch(page); const dialog = page.getByRole("dialog"); await dialog.getByRole("button", { name: "Create exact batch quote" }).click(); await reviewEveryJob(page);
  await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeEnabled(); f.applications[1].package_digest = "f".repeat(64); f.applications[1].artifact!.filename = "Changed-not-approved.docx";
  await dialog.getByRole("button", { name: "Refresh batch status" }).click(); await expect(dialog.getByRole("alert").filter({ hasText: "An application no longer matches" })).toBeVisible(); await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeDisabled(); await expect(dialog.getByText("Changed-not-approved.docx", { exact: true })).toHaveCount(0); expect(f.requests.some((request) => /\/(approve|execute)$/.test(request.path))).toBe(false); expect(f.unexpected).toEqual([]); await layout(page);
});

test("expired bookmarked batches cannot be approved or queued", async ({ page, context, baseURL }) => {
  const f = fixture(); f.batch = quotedBatch(f.applications); f.batch.expires_at = "2020-01-01T00:00:00Z"; await f.install(context, baseURL!); await page.goto("/employer-jobs/batches/batch_local_fixture");
  const dialog = page.getByRole("dialog"); await expect(dialog.getByRole("alert").filter({ hasText: "A quote expired" })).toBeVisible(); await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeDisabled(); expect(f.requests.every((request) => request.method === "GET")).toBe(true); expect(f.unexpected).toEqual([]); await layout(page);
});

test("manual portals remain individual handoffs and canonical duplicates cannot be quoted", async ({ page, context, baseURL }) => {
  const f = fixture(); f.applications.forEach((app) => { app.application_mode = "manual"; }); await f.install(context, baseURL!); await page.goto("/employer-jobs"); await expect(page.getByRole("button", { name: "Review selected batch (0)" })).toBeDisabled(); await expect(page.getByRole("checkbox", { name: /^Select / })).toHaveCount(0); await expect(page.getByText("Individual manual handoff. This employer is not included in API batches.", { exact: true })).toHaveCount(2);
  f.applications.forEach((app) => { app.application_mode = "api"; }); f.applications[1].opening_key = f.applications[0].opening_key; f.applications[1].admission_snapshot!.opening_key = f.applications[0].opening_key!; await selectBatch(page); const dialog = page.getByRole("dialog"); await expect(dialog.getByRole("alert").filter({ hasText: "one representation of each employer opening" })).toBeVisible(); await expect(dialog.getByRole("button", { name: "Create exact batch quote" })).toBeDisabled(); expect(f.requests.every((request) => request.method === "GET")).toBe(true); expect(f.unexpected).toEqual([]); await layout(page);
});

test("account switching clears the batch selection, exact-file review and cached quote", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await selectBatch(page); const dialog = page.getByRole("dialog"); await dialog.getByRole("button", { name: "Create exact batch quote" }).click(); await reviewEveryJob(page); await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeEnabled();
  f.switchOwner(); await page.evaluate(() => window.dispatchEvent(new StorageEvent("storage", { key: "nextauth.message", newValue: JSON.stringify({ event: "session", data: { trigger: "signIn" } }) })));
  await expect(page.getByRole("dialog")).toHaveCount(0); await expect(page.getByRole("button", { name: "Review selected batch (0)" })).toBeDisabled(); await expect(page.getByRole("button", { name: /Backend Engineer Example Studio/ })).toHaveCount(0); expect(f.requests.filter((request) => request.owner === "b").every((request) => request.method === "GET" && !request.path.includes("application-batches") && !request.path.includes("/applications/"))).toBe(true); expect(f.unexpected).toEqual([]); await layout(page);
});

test("withdrawing the source API route disables an already reviewed batch", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await selectBatch(page); const dialog = page.getByRole("dialog"); await dialog.getByRole("button", { name: "Create exact batch quote" }).click(); await reviewEveryJob(page);
  await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeEnabled(); f.catalog.sources[0].application_mode = "manual";
  await dialog.getByRole("button", { name: "Close batch review" }).click(); await page.goto("/employer-jobs/batches/batch_local_fixture"); await expect(dialog.getByRole("alert").filter({ hasText: "no longer offers a current permissioned API route" })).toBeVisible(); await expect(dialog.getByRole("button", { name: "Approve these 2 exact applications" })).toBeDisabled(); expect(f.requests.some((request) => /\/(approve|execute)$/.test(request.path))).toBe(false); expect(f.unexpected).toEqual([]); await layout(page);
});
