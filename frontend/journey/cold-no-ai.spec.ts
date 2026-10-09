import AxeBuilder from "@axe-core/playwright";
import { createHash, createHmac } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, request as requestFactory, type Route } from "@playwright/test";

test("fresh candidate: real Chromium → BFF → API → SQL, no AI and reviewed manual handoff", async ({ page, context, baseURL }) => {
  test.skip(!process.env.COLD_BROWSER_FRONTEND_URL, "Run only through the disposable Python orchestrator");
  const external: string[] = [];
  const failures: string[] = [];
  const backendFailures: number[] = [];
  const responses: { path: string; status: number }[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/backend/")) {
      responses.push({ path: path.replace(/\/(?:opp|app|search|approval|artifact|order|source)_[^/]+/g, "/:id"), status: response.status() });
      if (response.status() >= 500) backendFailures.push(response.status());
    }
  });
  // This is the single browser-side external provider substitution. Same-origin
  // UI, NextAuth and BFF requests always reach their real implementations.
  const guardExternal = async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.origin === baseURL || url.protocol === "blob:") return route.continue();
    if (url.href === "https://checkout.razorpay.com/v1/checkout.js") {
      external.push("synthetic_checkout_script");
      const signature = createHmac("sha256", "synthetic-browser-order-secret").update("order_synthetic_browser|pay_synthetic_browser").digest("hex");
      return route.fulfill({ contentType: "application/javascript", body: `window.Razorpay = class { constructor(options) { this.options = options; } on() {} open() { this.options.handler({razorpay_order_id:"order_synthetic_browser",razorpay_payment_id:"pay_synthetic_browser",razorpay_signature:"${signature}"}); } };` });
    }
    external.push("blocked_unexpected_browser_egress");
    return route.abort("blockedbyclient");
  };
  await context.route((url) => url.origin !== baseURL, guardExternal);
  const request = context.request;
  const original = await readFile(process.env.COLD_BROWSER_ORIGINAL!);
  const custom = await readFile(process.env.COLD_BROWSER_CUSTOM!);
  const anonymous = await request.get(`${baseURL}/api/backend/resume/list`);
  expect(anonymous.status()).toBe(401);
  const providers = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/auth/providers");
  await page.goto("/register");
  await providers; // The real mount effect confirms hydrated event handlers.
  await page.getByLabel("Email address", { exact: true }).fill("candidate.browser@example.com");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-browser-password-123");
  await page.getByRole("checkbox", { name: /I am at least 18/ }).check();
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page).toHaveURL(/\/resume$/);
  await expect(page.getByRole("checkbox", { name: /Optional AI skill enrichment/ })).not.toBeChecked();
  await page.locator('input[type="file"]').setInputFiles(process.env.COLD_BROWSER_ORIGINAL!);
  const parseResponse = page.waitForResponse((r) => r.url().endsWith("/api/backend/resume/parse") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Parse resume" }).click();
  const parsed = await (await parseResponse).json();
  expect(parsed.extraction_mode).toBe("deterministic");
  expect(parsed.enrichment_state).toBe("not_requested");
  const source = await request.get(`${baseURL}/api/backend/resume/${parsed.resume_id}/source`);
  expect(source.ok()).toBeTruthy();
  expect(await source.body()).toEqual(original);
  await page.goto("/profile");
  await page.getByLabel("Full name", { exact: true }).fill("Synthetic Candidate");
  await page.getByLabel("Target role", { exact: true }).fill("Python Engineer");
  await page.getByLabel("Location", { exact: true }).fill("Bengaluru, India");
  await page.getByLabel("Preferred location", { exact: true }).fill("Bengaluru");
  await page.getByLabel("Skills", { exact: true }).fill("Python, PostgreSQL, Docker");
  await page.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  const profile = await request.get(`${baseURL}/api/backend/auth/profile`);
  expect((await profile.json()).target_role).toBe("Python Engineer");
  await page.goto("/employer-jobs");
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption(String(parsed.resume_id));
  await page.getByLabel("Target role", { exact: true }).fill("Python Engineer");
  await page.getByLabel("Location", { exact: true }).fill("Bengaluru");
  await page.getByLabel("Jobs to find", { exact: true }).fill("3");
  await expect(page.getByRole("button", { name: "Find jobs", exact: true })).toBeDisabled();
  const initialCatalog = await request.get(`${baseURL}/api/backend/v1/employer-jobs/catalog`);
  expect((await initialCatalog.json()).balance).toBe(0);
  await page.goto("/billing");
  // Explicitly select the credit pack, independently of billing deep links.
  const pack = page.locator("article").filter({ hasText: "500 credits" });
  await expect(pack).toBeVisible();
  const select = pack.getByRole("button", { name: "Review purchase", exact: true });
  // The pack renders before account status resolves. Wait for its enabled
  // action; an instantaneous count can skip selection while it says "Checking status".
  await expect(select).toBeEnabled();
  await select.click();
  await expect(pack.getByRole("button", { name: "Selected", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: /I confirm my billing country/ }).check();
  const orderResponse = page.waitForResponse((r) => r.url().endsWith("/api/backend/billing/orders") && r.request().method() === "POST");
  const callbackResponse = page.waitForResponse((r) => r.url().includes("/checkout-result") && r.request().method() === "POST");
  await page.getByRole("button", { name: /Pay with Razorpay/ }).click();
  const checkout = await (await orderResponse).json();
  const callback = await callbackResponse;
  expect(callback.ok()).toBeTruthy();
  expect((await callback.json()).fulfilled).toBe(false);
  const beforeWebhook = await request.get(`${baseURL}/api/backend/v1/employer-jobs/catalog`);
  expect((await beforeWebhook.json()).balance).toBe(0);
  // Explicit synthetic provider capture, through the actual signed public webhook.
  const payload = { event: "payment.captured", payload: { payment: { entity: {
    id: "pay_synthetic_browser", entity: "payment", amount: checkout.amount_minor,
    currency: "INR", status: "captured", captured: true, order_id: checkout.provider_order_id,
    method: "upi", international: false, fee: 2500, tax: 381,
    notes: { hirewiz_order_id: checkout.order_id, sku: "job_service_500", billing_country: "IN" },
  } } } };
  const raw = JSON.stringify(payload);
  const signature = createHmac("sha256", "synthetic-browser-webhook-secret").update(raw).digest("hex");
  const provider = await requestFactory.newContext(); // No candidate cookies at the provider boundary.
  try { for (let i = 0; i < 2; i++) {
    const webhook = await provider.post(`${process.env.COLD_BROWSER_BACKEND_URL}/api/billing/webhooks/razorpay`, { data: raw, headers: { "content-type": "application/json", "x-razorpay-signature": signature, "x-razorpay-event-id": "evt_synthetic_browser" } });
    expect(webhook.ok()).toBeTruthy();
  } } finally { await provider.dispose(); }
  await expect(page.getByRole("heading", { name: "Payment confirmed", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Continue to employer jobs", exact: true }).click();
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption(String(parsed.resume_id));
  await page.getByLabel("Target role", { exact: true }).fill("Python Engineer");
  await page.getByLabel("Location", { exact: true }).fill("Bengaluru");
  await page.getByLabel("Jobs to find", { exact: true }).fill("3");
  await page.getByRole("combobox", { name: "Published/released within", exact: true }).selectOption("7");
  const searchResponse = page.waitForResponse((r) => r.url().endsWith("/api/backend/v1/employer-jobs/searches") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Find jobs", exact: true }).click();
  const search = await (await searchResponse).json();
  expect(search.desired_count).toBe(3); expect(search.delivered_count).toBe(2);
  expect(search.reserved_credits).toBe(6); expect(search.charged_credits).toBe(4); expect(search.refunded_credits).toBe(2);
  await expect(page.getByText("2 of 3 requested jobs", { exact: true })).toBeVisible();
  await expect(page.getByText("Basic skill fit", { exact: false }).first()).toBeVisible();
  const createResponse = page.waitForResponse((r) => r.url().endsWith("/api/backend/v1/employer-jobs/applications") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Prepare application", exact: true }).first().click();
  let application = await (await createResponse).json();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const approve = dialog.getByRole("button", { name: "Approve and prepare handoff", exact: true });
  const reviewed = dialog.getByRole("checkbox", { name: /I reviewed the saved destination/ });
  await expect(approve).toBeDisabled();
  await expect(dialog.getByRole("link", { name: "Open employer application", exact: true })).toHaveCount(0);
  await dialog.getByLabel("First name", { exact: false }).fill("Synthetic");
  await dialog.getByLabel("Last name", { exact: false }).fill("Candidate");
  await dialog.getByLabel("Email", { exact: false }).fill("candidate.browser@example.com");
  const saved = page.waitForResponse((r) => r.url().endsWith("/package") && r.request().method() === "PUT");
  await dialog.getByRole("button", { name: "Save and refresh review", exact: true }).click();
  application = await (await saved).json();
  await expect(reviewed).toBeEnabled();
  await expect(dialog.getByLabel("Saved handoff package")).toContainText("Synthetic");
  const firstDownload = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Download review file", exact: true }).click();
  const artifactPath = await (await firstDownload).path();
  expect(await readFile(artifactPath!)).toEqual(original);
  await reviewed.check();
  const executed = page.waitForResponse((r) => r.url().endsWith("/execute") && r.request().method() === "POST");
  await approve.click();
  const firstHandoff = await (await executed).json();
  expect(firstHandoff.status).toBe("manual_handoff"); expect(firstHandoff.charged_credits).toBe(0); expect(firstHandoff.receipt).toBeNull();
  await expect(dialog.getByRole("status").filter({ hasText: "Reviewed handoff prepared" })).toBeVisible();
  await dialog.getByRole("combobox", { name: "Resume choice", exact: true }).selectOption("custom");
  await dialog.getByLabel("Upload a custom PDF or DOCX", { exact: true }).setInputFiles(process.env.COLD_BROWSER_CUSTOM!);
  await expect(dialog.getByText(/Save your changes to refresh/)).toBeVisible();
  await expect(approve).toBeDisabled();
  const savedCustom = page.waitForResponse((r) => r.url().endsWith("/package") && r.request().method() === "PUT");
  await dialog.getByRole("button", { name: "Save and refresh review", exact: true }).click();
  application = await (await savedCustom).json();
  expect(application.package_digest).not.toBe(firstHandoff.package_digest);
  const staleExecute = await request.post(`${baseURL}/api/backend/v1/employer-jobs/applications/${application.id}/execute`, { data: { package_digest: firstHandoff.package_digest }, headers: { Origin: baseURL! } });
  expect(staleExecute.status()).toBe(409);
  await expect(reviewed).not.toBeChecked();
  await expect(approve).toBeDisabled();
  await expect(dialog.getByLabel("Saved handoff package")).toContainText("custom");
  const customArtifact = await request.get(`${baseURL}/api/backend/v1/employer-jobs/applications/${application.id}/artifact`);
  expect(await customArtifact.body()).toEqual(custom);
  await reviewed.check();
  const finalResponse = page.waitForResponse((r) => r.url().endsWith("/execute") && r.request().method() === "POST");
  await approve.click();
  const final = await (await finalResponse).json();
  expect(final.status).toBe("manual_handoff"); expect(final.charged_credits).toBe(0); expect(final.receipt).toBeNull();
  await expect(dialog.getByRole("link", { name: "Open employer application", exact: true })).toHaveAttribute("href", final.posting.apply_url);
  await expect(dialog.getByText("Reviewed handoff prepared. This application has not been submitted.", { exact: true })).toBeVisible();
  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect((await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    if (width === 390 || width === 1440) {
      await dialog.locator(".overflow-y-auto").evaluate((element) => { element.scrollTop = element.scrollHeight; });
      await dialog.screenshot({ path: `${process.env.COLD_BROWSER_EVIDENCE}/manual-handoff-${width}.png` });
    }
  }
  // Independent second real browser identity must not retrieve the first owner's file/package.
  const outsider = await context.browser()!.newContext();
  await outsider.route((url) => url.origin !== baseURL, guardExternal);
  const otherPage = await outsider.newPage();
  const otherProviders = otherPage.waitForResponse((response) => new URL(response.url()).pathname === "/api/auth/providers");
  await otherPage.goto(`${baseURL}/register`);
  await otherProviders;
  await otherPage.getByLabel("Email address", { exact: true }).fill("outsider.browser@example.com");
  await otherPage.getByLabel("Password", { exact: true }).fill("synthetic-browser-password-123");
  await otherPage.getByRole("checkbox", { name: /I am at least 18/ }).check();
  await otherPage.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(otherPage).toHaveURL(/\/resume$/);
  for (const path of [`resume/${parsed.resume_id}/source`, `v1/employer-jobs/applications/${application.id}`, `v1/employer-jobs/applications/${application.id}/artifact`]) {
    expect((await outsider.request.get(`${baseURL}/api/backend/${path}`)).status()).toBe(404);
  }
  await outsider.close();
  expect(failures).toEqual([]); expect(backendFailures).toEqual([]);
  expect(external).toEqual(["synthetic_checkout_script"]);
  await writeFile(process.env.COLD_BROWSER_BROWSER_PROOF!, JSON.stringify({
    source_bytes_exact: true, custom_bytes_exact: true, original_sha256: createHash("sha256").update(original).digest("hex"),
    custom_sha256: createHash("sha256").update(custom).digest("hex"), cold_user_created_via_ui: true,
    real_nextauth: true, no_bff_interception: true, no_credits_before_webhook: true,
    stale_approval_rejected: true, cross_owner_denied: true, final_status: final.status,
    search: { desired: 3, delivered: 2, reserved: 6, charged: 4, released: 2 },
    automatic_application_charged: 0, employer_submit_receipt: null, external,
    reviewed_widths: [320, 390, 768, 1024, 1440], axe_violations: 0,
    page_errors: failures.length, server_errors: backendFailures.length, bff_responses: responses,
  }, null, 2) + "\n");
});
