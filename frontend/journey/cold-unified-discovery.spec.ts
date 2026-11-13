import AxeBuilder from "@axe-core/playwright";
import { createHmac } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, request as requestFactory } from "@playwright/test";
import { formatCreditPrice, parseUnifiedPurchaseQuote, sameUnifiedPurchaseQuote } from "../lib/unifiedCredits";

test("unified pass and reviewed native discovery use actual BFF API SQL with zero AI", async ({ page, context, baseURL }) => {
  test.skip(process.env.COLD_BROWSER_SCENARIO !== "unified", "Run through the isolated synthetic Python orchestrator");
  const external: string[] = [], errors: string[] = [], serverErrors: number[] = [];
  const checkoutRequests: string[] = [], orderReads: string[] = [], checkoutCallbacks: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (request.method() === "POST" && pathname === "/api/backend/v1/credits/orders") checkoutRequests.push(pathname);
    if (request.method() === "GET" && pathname.startsWith("/api/backend/v1/credits/orders/")) orderReads.push(pathname);
    if (request.method() === "POST" && /^\/api\/backend\/billing\/orders\/[^/]+\/checkout-result$/.test(pathname)) checkoutCallbacks.push(pathname);
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => { if (new URL(response.url()).pathname.startsWith("/api/backend/") && response.status() >= 500) serverErrors.push(response.status()); });
  await context.route((url) => url.origin !== baseURL, async (route) => {
    const url = new URL(route.request().url());
    if (url.protocol === "blob:") return route.continue();
    if (url.href === "https://checkout.razorpay.com/v1/checkout.js") {
      external.push("synthetic_checkout_script");
      const signature = createHmac("sha256", "synthetic-browser-order-secret").update("order_synthetic_browser|pay_synthetic_browser").digest("hex");
      return route.fulfill({ contentType: "application/javascript", body: `
        window.syntheticCheckout = { opens: [] };
        window.Razorpay = class {
          constructor(options) { this.options = options; }
          on() {}
          open() {
            window.syntheticCheckout.opens.push(this.options.order_id);
            window.syntheticCheckout.dismiss = () => this.options.modal.ondismiss();
            window.syntheticCheckout.confirm = () => this.options.handler({
              razorpay_order_id: "order_synthetic_browser", razorpay_payment_id: "pay_synthetic_browser", razorpay_signature: "${signature}"
            });
          }
        };
      ` });
    }
    external.push("blocked_unexpected_browser_egress"); return route.abort("blockedbyclient");
  });
  const request = context.request;
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1"); expect(new URL(baseURL!).protocol).toBe("https:");
  const backend = new URL(process.env.COLD_BROWSER_BACKEND_URL!);
  expect(backend.hostname).toBe("127.0.0.1"); expect(backend.protocol).toBe("https:");
  async function sendProviderEvent(event: unknown, id: string, expectedStatus: "processed" | "duplicate") {
    const raw = JSON.stringify(event), signature = createHmac("sha256", "synthetic-browser-webhook-secret").update(raw).digest("hex");
    const provider = await requestFactory.newContext({ ignoreHTTPSErrors: true, maxRedirects: 0 });
    try {
      const response = await provider.post(new URL("/api/billing/webhooks/razorpay", backend).href, {
        data: raw, headers: { "content-type": "application/json", "x-razorpay-signature": signature, "x-razorpay-event-id": id },
      });
      expect(response.ok()).toBeTruthy();
      expect(await response.json()).toEqual({ ok: true, status: expectedStatus });
    } finally { await provider.dispose(); }
  }
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
  const india = page.getByRole("checkbox", { name: /I confirm my billing country/ });
  await india.check();
  const orderResponse = page.waitForResponse((response) => response.url().endsWith("/api/backend/v1/credits/orders") && response.request().method() === "POST");
  await pay.click();
  const created = await orderResponse;
  expect(created.ok()).toBeTruthy();
  const order = await created.json();
  expect(order.accepted_quote).toMatchObject({ sku: "starter_pass", amount_minor: 7900, credits: 40, auto_renews: false, duration_days: 30 });
  expect(order.accepted_quote.eligible_services).toHaveLength(1);
  expect(order.accepted_quote.eligible_services[0]).toMatchObject({ operation: "job_discovery_batch", credits: 1 });
  const orderPath = `/api/backend/v1/credits/orders/${encodeURIComponent(order.order_id)}`;
  const callbackPath = `/api/backend/billing/orders/${encodeURIComponent(order.order_id)}/checkout-result`;
  type SyntheticCheckoutWindow = Window & { syntheticCheckout?: { opens: string[]; dismiss: () => void; confirm: () => void } };
  const hostedOpens = () => page.evaluate(() => (window as SyntheticCheckoutWindow).syntheticCheckout?.opens ?? []);
  await expect.poll(hostedOpens).toEqual([order.provider_order_id]);
  expect(orderReads).toEqual([orderPath]);
  await page.evaluate(() => (window as SyntheticCheckoutWindow).syntheticCheckout!.dismiss());
  await expect(page.getByText("Checkout closed. No payment is assumed. Check the original payment before trying again.", { exact: true })).toBeVisible();
  await expect(india).not.toBeChecked();
  await india.check();
  const resume = page.getByRole("button", { name: "Resume original checkout", exact: true });
  await expect(resume).toBeDisabled();
  await expect(page.getByRole("button", { name: "Review Starter pass", exact: true })).toBeDisabled();

  const callbackResponse = page.waitForResponse((response) => new URL(response.url()).pathname === callbackPath && response.request().method() === "POST");
  await page.evaluate(() => {
    const hosted = (window as SyntheticCheckoutWindow).syntheticCheckout!;
    hosted.confirm();
    hosted.confirm();
  });
  const callback = await callbackResponse;
  expect(callback.ok()).toBeTruthy();
  expect(await callback.json()).toEqual({ accepted: true, status: "pending", fulfilled: false });
  expect((await wallet()).available_credits).toBe(0);
  expect(checkoutCallbacks).toEqual([callbackPath]);
  expect(orderReads).toEqual([orderPath]);
  await expect(resume).toBeDisabled();
  await expect(page.getByRole("heading", { name: "Credit payment confirmed", exact: true })).toHaveCount(0);

  async function checkOriginalOrder() {
    const status = page.waitForResponse((response) => new URL(response.url()).pathname === orderPath && response.request().method() === "GET");
    await page.getByRole("button", { name: "Check payment status", exact: true }).click();
    const checked = await status;
    expect(checked.ok()).toBeTruthy();
    const value = await checked.json();
    expect(value).toMatchObject({ order_id: order.order_id, provider_order_id: order.provider_order_id });
    return value;
  }
  expect(await checkOriginalOrder()).toMatchObject({ status: "pending", fulfilled: false, checkout_available: false });
  await expect(resume).toBeDisabled();
  const capture = { event: "payment.captured", payload: { payment: { entity: { id: "pay_synthetic_browser", entity: "payment", amount: 7900,
    currency: "INR", status: "captured", captured: true, order_id: order.provider_order_id, method: "upi", international: false, fee: 186, tax: 28,
    notes: { hirewiz_order_id: order.order_id, sku: "starter_pass", billing_country: "IN" } } } } };
  await sendProviderEvent(capture, "evt_synthetic_unified", "processed");
  await sendProviderEvent(capture, "evt_synthetic_unified", "duplicate");
  const deliveredOrderResponse = await request.get(`${baseURL}/api/backend/v1/credits/orders/${encodeURIComponent(order.order_id)}`);
  expect(deliveredOrderResponse.ok()).toBeTruthy();
  const deliveredOrder = await deliveredOrderResponse.json();
  expect(deliveredOrder).toMatchObject({
    order_id: order.order_id, provider_order_id: order.provider_order_id, status: "paid", fulfilled: true,
  });
  const deliveredQuote = parseUnifiedPurchaseQuote(deliveredOrder.accepted_quote);
  const acceptedQuote = parseUnifiedPurchaseQuote(order.accepted_quote);
  expect(deliveredQuote).not.toBeNull(); expect(acceptedQuote).not.toBeNull();
  // Only the read-time server clock changes; every accepted purchase term stays exact.
  expect(sameUnifiedPurchaseQuote(deliveredQuote!, acceptedQuote!)).toBe(true);
  expect((await wallet()).available_credits).toBe(40);
  expect(await checkOriginalOrder()).toMatchObject({ status: "paid", fulfilled: true });
  await expect(page.getByText("Payment confirmed. Your purchased credits have been delivered.", { exact: true })).toBeVisible({ timeout: 15_000 });
  expect(checkoutRequests).toEqual(["/api/backend/v1/credits/orders"]);
  expect(checkoutCallbacks).toEqual([callbackPath]);
  expect(orderReads).toEqual([orderPath, orderPath, orderPath]);
  expect(await hostedOpens()).toEqual([order.provider_order_id]);
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
  const refund = (id: string, amount: number) => ({ event: "refund.processed", payload: {
    payment: { entity: { ...capture.payload.payment.entity, status: "refunded" } },
    refund: { entity: { id, entity: "refund", payment_id: capture.payload.payment.entity.id, amount, currency: "INR", status: "processed" } },
  } });
  const partial = refund("rfnd_synthetic_browser_half", 3950);
  await sendProviderEvent(partial, "evt_synthetic_refund_half", "processed");
  expect(await wallet()).toMatchObject({ available_credits: 19, reserved_credits: 0, debt_credits: 0 });
  await sendProviderEvent(partial, "evt_synthetic_refund_half", "duplicate");
  expect(await wallet()).toMatchObject({ available_credits: 19, reserved_credits: 0, debt_credits: 0 });
  await page.goto("/billing");
  await expect(page.getByText(`Refunded: ${formatCreditPrice(3950)}`, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Credit payment confirmed", exact: true })).toBeVisible();

  const rest = refund("rfnd_synthetic_browser_rest", 3950);
  await sendProviderEvent(rest, "evt_synthetic_refund_rest", "processed");
  await sendProviderEvent(rest, "evt_synthetic_refund_rest", "duplicate");
  expect(await wallet()).toMatchObject({ available_credits: 0, reserved_credits: 0, debt_credits: 1 });
  const refundRead = await request.get(`${baseURL}/api/backend/v1/credits/orders/${encodeURIComponent(order.order_id)}`);
  expect(refundRead.ok()).toBeTruthy();
  const refundedOrder = await refundRead.json();
  expect(refundedOrder).toMatchObject({ order_id: order.order_id, status: "refunded", fulfilled: false,
    refunded_amount_minor: 7900, checkout_available: false });
  const refundedQuote = parseUnifiedPurchaseQuote(refundedOrder.accepted_quote);
  expect(refundedQuote).not.toBeNull();
  expect(sameUnifiedPurchaseQuote(refundedQuote!, acceptedQuote!)).toBe(true);
  await page.reload();
  await expect(page.getByText("This payment was refunded. Review the refund and balance in your history.", { exact: true })).toBeVisible();
  await expect(page.getByText(`Refunded: ${formatCreditPrice(7900)}`, { exact: true })).toBeVisible();
  const pendingAdjustment = page.getByText("Pending balance adjustment", { exact: true });
  await expect(pendingAdjustment.locator("..").getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: /revoke.*-20/ })).toHaveCount(2);
  const profile = await (await request.get(`${baseURL}/api/account/profile`)).json();
  expect(profile.ai_credits).toBe(0);
  expect((await (await request.get(`${baseURL}/api/backend/v1/employer-jobs/applications`)).json()).items).toEqual([]);
  expect(errors).toEqual([]); expect(serverErrors).toEqual([]); expect(external).toEqual(["synthetic_checkout_script"]);
  await writeFile(process.env.COLD_BROWSER_BROWSER_PROOF!, JSON.stringify({ scenario: "unified", real_nextauth: true, no_bff_interception: true,
    synthetic_policy_only: true, production_release_evidence: false, secure_http_only_session_cookie: true,
    new_pass_reviewed_via_ui: true, payment_granted_only_after_webhook: true, duplicate_capture_granted_once: true,
    dismissed_checkout_requires_fresh_status: true, late_callback_deduplicated_without_automatic_read_or_reopen: true,
    bought_credits: 40, remaining_before_refund: 39, remaining_credits: 0, refunded_minor: 7900,
    refund_debt_credits: 1, duplicate_refunds_revoked_once: true, original_quote_preserved: true,
    native_partial_batch: { requested: 5, delivered: 2, charged: 1 },
    native_empty_batch: { delivered: 0, charged: 0, restored: 1 }, same_intent_replay_no_charge: true,
    explicit_second_batch_review: true, legacy_analysis_units_unchanged: 0, applications_created: 0,
    model_calls: 0, external, page_errors: errors.length, server_errors: serverErrors.length,
    reviewed_widths: [390, 1440], axe_violations: 0 }, null, 2) + "\n");
});
