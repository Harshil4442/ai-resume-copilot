import AxeBuilder from "@axe-core/playwright";
import { createHmac } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, request as requestFactory } from "@playwright/test";

test("unified pass and reviewed native discovery use actual BFF API SQL with zero AI", async ({ page, context, baseURL }) => {
  test.skip(process.env.COLD_BROWSER_SCENARIO !== "unified", "Run through the isolated synthetic Python orchestrator");
  const external: string[] = [], errors: string[] = [], serverErrors: number[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => { if (new URL(response.url()).pathname.startsWith("/api/backend/") && response.status() >= 500) serverErrors.push(response.status()); });
  await context.route((url) => url.origin !== baseURL, async (route) => {
    const url = new URL(route.request().url());
    if (url.protocol === "blob:") return route.continue();
    if (url.href === "https://checkout.razorpay.com/v1/checkout.js") {
      external.push("synthetic_checkout_script");
      const signature = createHmac("sha256", "synthetic-browser-order-secret").update("order_synthetic_browser|pay_synthetic_browser").digest("hex");
      return route.fulfill({ contentType: "application/javascript", body: `window.Razorpay = class { constructor(options) { this.options = options; } on() {} open() { this.options.handler({razorpay_order_id:"order_synthetic_browser",razorpay_payment_id:"pay_synthetic_browser",razorpay_signature:"${signature}"}); } };` });
    }
    external.push("blocked_unexpected_browser_egress"); return route.abort("blockedbyclient");
  });
  const request = context.request;
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1"); expect(new URL(baseURL!).protocol).toBe("https:");
  const providers = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/auth/providers");
  await page.goto("/register"); await providers;
  await page.getByLabel("Email address", { exact: true }).fill("candidate.unified@example.com");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-browser-password-123");
  await page.getByRole("checkbox", { name: /I am at least 18/ }).check();
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page).toHaveURL(/\/resume$/);
  const session = (await context.cookies()).filter((cookie) => cookie.name === "__Secure-next-auth.session-token");
  expect(session).toHaveLength(1); expect(session[0]).toMatchObject({ secure: true, httpOnly: true });
  const initialProfile = await request.get(`${baseURL}/api/account/profile`);
  expect(initialProfile.ok()).toBeTruthy();
  expect((await initialProfile.json()).ai_credits).toBe(0);
  await page.locator('input[type="file"]').setInputFiles(process.env.COLD_BROWSER_ORIGINAL!);
  const parseResponse = page.waitForResponse((response) => response.url().endsWith("/api/backend/resume/parse") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Parse resume/ }).click();
  const parsedResponse = await parseResponse; expect(parsedResponse.ok()).toBeTruthy(); const parsed = await parsedResponse.json();
  expect(parsed.extraction_mode).toBe("deterministic"); expect(parsed.enrichment_units).toBe(0);
  const wallet = async () => (await request.get(`${baseURL}/api/backend/v1/credits/wallet`)).json();
  expect((await wallet()).available_credits).toBe(0);

  await page.goto("/billing");
  await page.getByRole("button", { name: "Review Starter pass", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Review your Starter pass", exact: true })).toBeVisible();
  await expect(page.getByText("Employer job discovery · 1 credit per approved operation", { exact: true })).toHaveCount(2);
  const pay = page.getByRole("button", { name: "Continue with ₹79 payment", exact: true });
  await expect(pay).toBeDisabled();
  await page.getByRole("checkbox", { name: /I confirm my billing country/ }).check();
  const orderResponse = page.waitForResponse((response) => response.url().endsWith("/api/backend/v1/credits/orders") && response.request().method() === "POST");
  const callbackResponse = page.waitForResponse((response) => response.url().includes("/checkout-result") && response.request().method() === "POST");
  await pay.click(); const order = await (await orderResponse).json();
  expect(order.accepted_quote).toMatchObject({ sku: "starter_pass", amount_minor: 7900, credits: 40, auto_renews: false, duration_days: 30 });
  expect(order.accepted_quote.eligible_services).toHaveLength(1);
  expect(order.accepted_quote.eligible_services[0]).toMatchObject({ operation: "job_discovery_batch", credits: 1 });
  expect((await (await callbackResponse).json()).fulfilled).toBe(false); expect((await wallet()).available_credits).toBe(0);
  const capture = { event: "payment.captured", payload: { payment: { entity: { id: "pay_synthetic_browser", entity: "payment", amount: 7900,
    currency: "INR", status: "captured", captured: true, order_id: order.provider_order_id, method: "upi", international: false, fee: 186, tax: 28,
    notes: { hirewiz_order_id: order.order_id, sku: "starter_pass", billing_country: "IN" } } } } };
  const raw = JSON.stringify(capture), signature = createHmac("sha256", "synthetic-browser-webhook-secret").update(raw).digest("hex");
  const provider = await requestFactory.newContext({ ignoreHTTPSErrors: true });
  try { for (let index = 0; index < 2; index++) expect((await provider.post(`${process.env.COLD_BROWSER_BACKEND_URL}/api/billing/webhooks/razorpay`, {
    data: raw, headers: { "content-type": "application/json", "x-razorpay-signature": signature, "x-razorpay-event-id": "evt_synthetic_unified" },
  })).ok()).toBeTruthy(); } finally { await provider.dispose(); }
  await expect(page.getByText("Payment confirmed. Your purchased credits have been delivered.", { exact: true })).toBeVisible();
  expect((await wallet()).available_credits).toBe(40);
  await page.goto("/employer-jobs");
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption(String(parsed.resume_id));
  await page.getByLabel("Target role", { exact: true }).fill("Python Engineer");
  await page.getByLabel("Location", { exact: true }).fill("Bengaluru");
  await page.getByRole("combobox", { name: "Published/released within", exact: true }).selectOption("7");
  await expect(page.getByRole("button", { name: "Find jobs", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Review one-credit search", exact: true }).click();
  await expect(page.getByText(/A completed batch with 1–4 new verified jobs still costs one credit/)).toBeVisible();
  expect((await wallet()).available_credits).toBe(40);
  const firstResponse = page.waitForResponse((response) => response.url().endsWith("/api/backend/v1/credits/discovery-batches") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Approve 1 credit", exact: true }).click();
  const first = await firstResponse, accepted = first.request().postDataJSON(), firstBatch = await first.json();
  expect(first.ok()).toBeTruthy(); expect(accepted).toMatchObject({ desired_count: 5, resume_id: parsed.resume_id });
  // The real status hook deliberately backs off to a 10-second read after its
  // initial cached+network reads. Wait for that bounded worker/status cycle.
  await expect(page.getByText("Partial batch: 2 of 5 requested jobs. The reviewed one-credit batch charge applies.", { exact: true })).toBeVisible({ timeout: 30_000 });
  expect((await wallet()).available_credits).toBe(39);
  const replay = await request.post(`${baseURL}/api/backend/v1/credits/discovery-batches`, { data: accepted, headers: { Origin: baseURL! } });
  expect(replay.ok()).toBeTruthy(); expect((await replay.json()).id).toBe(firstBatch.id); expect((await wallet()).available_credits).toBe(39);
  // Another batch requires another explicit quote and approval; already-delivered jobs are excluded.
  await page.getByRole("button", { name: "Review one-credit search", exact: true }).click();
  await page.getByRole("button", { name: "Approve 1 credit", exact: true }).click();
  await expect(page.getByText("0 credits charged · 0 held · 1 restored", { exact: true })).toBeVisible({ timeout: 30_000 });
  expect((await wallet()).available_credits).toBe(39);
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
  }
  const profile = await (await request.get(`${baseURL}/api/account/profile`)).json();
  expect(profile.ai_credits).toBe(0);
  expect((await (await request.get(`${baseURL}/api/backend/v1/employer-jobs/applications`)).json()).items).toEqual([]);
  expect(errors).toEqual([]); expect(serverErrors).toEqual([]); expect(external).toEqual(["synthetic_checkout_script"]);
  await writeFile(process.env.COLD_BROWSER_BROWSER_PROOF!, JSON.stringify({ scenario: "unified", real_nextauth: true, no_bff_interception: true,
    synthetic_policy_only: true, production_release_evidence: false, secure_http_only_session_cookie: true,
    new_pass_reviewed_via_ui: true, payment_granted_only_after_webhook: true, duplicate_capture_granted_once: true,
    bought_credits: 40, remaining_credits: 39, native_partial_batch: { requested: 5, delivered: 2, charged: 1 },
    native_empty_batch: { delivered: 0, charged: 0, restored: 1 }, same_intent_replay_no_charge: true,
    explicit_second_batch_review: true, legacy_analysis_units_unchanged: 0, applications_created: 0,
    model_calls: 0, external, page_errors: errors.length, server_errors: serverErrors.length,
    reviewed_widths: [390, 1440], axe_violations: 0 }, null, 2) + "\n");
});
