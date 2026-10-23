import AxeBuilder from "@axe-core/playwright";
import { createHash, createHmac } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, request as requestFactory, type Route } from "@playwright/test";

// Disposable CI lab measurements only. No production instrumentation or field-INP claim.
type PublicLabState = {
  supported: string[];
  lcp: number | null;
  shifts: { at: number; value: number }[];
  events: { at: number; duration: number; interactionId: number; name: string }[];
  truncated: boolean;
  firstHiddenAt: number | null;
  csp: { script: number; style: number; other: number };
  observers: PerformanceObserver[];
};
type PublicLabWindow = Window & { __hirewizPublicLab?: PublicLabState };

async function measurePublicLab(browser: import("@playwright/test").Browser, baseURL: string, guardExternal: (route: Route) => Promise<void>) {
  expect(new URL(baseURL).hostname).toBe("127.0.0.1");
  expect(new URL(baseURL).protocol).toBe("https:");
  const measurements = [];
  for (const width of [390, 1440]) for (const path of ["/", "/pricing"]) {
    const labContext = await browser.newContext({ ignoreHTTPSErrors: true }); // Owned CI loopback TLS only.
    await labContext.route((url) => url.origin !== baseURL, guardExternal);
    const labPage = await labContext.newPage();
    const errors = { page: 0, console: 0, request: 0, script: 0 };
    const increment = (key: keyof typeof errors) => { errors[key] = Math.min(255, errors[key] + 1); };
    labPage.on("pageerror", () => increment("page"));
    labPage.on("console", (message) => { if (message.type() === "error") increment("console"); });
    labPage.on("requestfailed", (request) => {
      increment("request");
      if (request.resourceType() === "script") increment("script");
    });
    let responseStatus: number | null = null;
    try {
      await labPage.setViewportSize({ width, height: 900 });
      await labPage.addInitScript(() => {
        const state: PublicLabState = { supported: [...PerformanceObserver.supportedEntryTypes],
          lcp: null, shifts: [], events: [], truncated: false, firstHiddenAt: null,
          csp: { script: 0, style: 0, other: 0 }, observers: [] };
        (window as PublicLabWindow).__hirewizPublicLab = state;
        document.addEventListener("securitypolicyviolation", (event) => {
          const key = event.effectiveDirective.startsWith("script-src") ? "script"
            : event.effectiveDirective.startsWith("style-src") ? "style" : "other";
          state.csp[key] = Math.min(255, state.csp[key] + 1);
        });
        document.addEventListener("visibilitychange", () => {
          if (document.visibilityState === "hidden" && state.firstHiddenAt === null) state.firstHiddenAt = performance.now();
        });
        for (const type of ["largest-contentful-paint", "layout-shift", "event"]) {
          if (!state.supported.includes(type)) continue;
          const receive = (entries: PerformanceEntry[]) => {
            for (const entry of entries) {
              if (type === "largest-contentful-paint") state.lcp = entry.startTime;
              if (type === "layout-shift") {
                const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
                if (!shift.hadRecentInput) {
                  if (state.shifts.length >= 1024) state.truncated = true;
                  else state.shifts.push({ at: shift.startTime, value: shift.value });
                }
              }
              if (type === "event") {
                const event = entry as PerformanceEntry & { interactionId: number };
                if (event.interactionId > 0) {
                  if (state.events.length >= 256) state.truncated = true;
                  else state.events.push({ at: event.startTime, duration: event.duration,
                    interactionId: event.interactionId, name: event.name });
                }
              }
            }
          };
          const observer = new PerformanceObserver((list) => receive(list.getEntries()));
          observer.observe({ type, buffered: true, ...(type === "event" ? { durationThreshold: 16 } : {}) });
          // Flush pending entries before reading the bounded result.
          state.observers.push(observer);
          Object.assign(observer, { flush: () => receive(observer.takeRecords()) });
        }
      });
      const response = await labPage.goto(`${baseURL}${path}`, { waitUntil: "load" });
      responseStatus = response?.status() ?? null;
      expect(response?.status()).toBe(200);
      await expect(labPage.locator("h1")).toHaveCount(1);
      await expect(labPage.locator("h1")).toBeVisible();
      await labPage.evaluate(() => document.fonts.ready);
      // Fixed observation window captures initial rendering/animation after load.
      await labPage.waitForTimeout(5000);
      const beforeInteraction = await labPage.evaluate(() => {
        const state = (window as PublicLabWindow).__hirewizPublicLab!;
        for (const observer of state.observers) (observer as PerformanceObserver & { flush: () => void }).flush();
        return { at: performance.now(), lcp: state.lcp, shifts: [...state.shifts], firstHiddenAt: state.firstHiddenAt };
      });
      let interaction = "not_sampled_on_pricing";
      if (path === "/") {
        if (width === 390) {
          await labPage.getByRole("button", { name: "Open navigation", exact: true }).click();
          await expect(labPage.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible();
          interaction = "trusted_mobile_menu_click";
        } else {
          await labPage.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", { name: "Benefits", exact: true }).click();
          await expect(labPage).toHaveURL(/#benefits$/);
          interaction = "trusted_benefits_link_click";
        }
        await labPage.waitForTimeout(250); // Allow Event Timing delivery after the next paint.
      }
      const metrics = await labPage.evaluate(({ beforeInteraction, interaction }) => {
        const state = (window as PublicLabWindow).__hirewizPublicLab!;
        for (const observer of state.observers) (observer as PerformanceObserver & { flush: () => void }).flush();
        const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
        let sessionStart = 0, lastShift = 0, sessionScore = 0, cls = 0;
        for (const shift of beforeInteraction.shifts.sort((a, b) => a.at - b.at)) {
          if (shift.at - lastShift >= 1000 || shift.at - sessionStart >= 5000) {
            sessionStart = shift.at; sessionScore = 0;
          }
          sessionScore += shift.value; lastShift = shift.at; cls = Math.max(cls, sessionScore);
        }
        const sampled = state.events.filter((entry) => entry.at >= beforeInteraction.at);
        const interactionDuration = sampled.length ? Math.max(...sampled.map((entry) => entry.duration)) : null;
        for (const observer of state.observers) observer.disconnect();
        return {
          navigation: navigation ? { type: navigation.type, ttfb_ms: navigation.responseStart - navigation.startTime,
            dom_content_loaded_ms: navigation.domContentLoadedEventEnd - navigation.startTime,
            load_ms: navigation.loadEventEnd - navigation.startTime, response_end_ms: navigation.responseEnd - navigation.startTime } : null,
          lcp_ms: beforeInteraction.lcp,
          cls_initial_window: state.supported.includes("layout-shift") ? cls : null,
          render_observation_end_ms: beforeInteraction.at, event_observation_end_ms: performance.now(), observation_after_load_ms: 5000,
          first_hidden_at_ms: beforeInteraction.firstHiddenAt,
          lcp_missing_reason: beforeInteraction.lcp === null ? (state.supported.includes("largest-contentful-paint") ? "no_lcp_entry_observed" : "entry_type_unsupported") : null,
          cls_missing_reason: state.supported.includes("layout-shift") ? null : "entry_type_unsupported",
          navigation_missing_reason: navigation ? null : "navigation_entry_not_reported",
          visibility: document.visibilityState, supported_entry_types: state.supported,
          interaction: { sample: interaction, event_duration_ms: interactionDuration, observed_event_entries: sampled.length,
            observer_threshold_ms: 16, field_inp: null,
            missing_reason: interactionDuration === null ? (interaction === "not_sampled_on_pricing" ? interaction : "no_entry_observed_at_16ms_threshold") : null },
          truncated: state.truncated,
        };
      }, { beforeInteraction, interaction });
      expect(metrics.visibility).toBe("visible");
      expect(metrics.first_hidden_at_ms).toBeNull();
      expect(metrics.truncated).toBe(false);
      if (metrics.navigation) {
        expect(metrics.navigation.type).toBe("navigate");
        expect(metrics.navigation.ttfb_ms).toBeGreaterThanOrEqual(0);
      }
      if (metrics.lcp_ms !== null) expect(metrics.lcp_ms).toBeGreaterThan(0);
      measurements.push({ path, width, height: 900, ...metrics });
    } catch (error) {
      // Fixed public-lab metadata only: never serialize DOM text, URLs or errors.
      try {
        const snapshotPromise = labPage.evaluate(() => {
          const heading = document.querySelector("h1");
          const ancestors = [];
          for (let node: Element | null = heading; node && ancestors.length < 8; node = node.parentElement) {
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            const bounded = (value: number) => Math.round(Math.max(-1_000_000, Math.min(1_000_000, value)) * 100) / 100;
            const display = ["none", "contents", "block", "inline", "inline-block", "flex", "inline-flex", "grid", "inline-grid"].includes(style.display) ? style.display : "other";
            const visibility = ["visible", "hidden", "collapse"].includes(style.visibility) ? style.visibility : "other";
            const contentVisibility = ["visible", "auto", "hidden"].includes(style.contentVisibility) ? style.contentVisibility : "other";
            ancestors.push({ hidden: node.hasAttribute("hidden"), display, visibility, content_visibility: contentVisibility,
              bounds: [rect.x, rect.y, rect.width, rect.height].map(bounded) });
          }
          return { ready_state: document.readyState, visibility: document.visibilityState,
            h1_count: Math.min(1024, document.querySelectorAll("h1").length), ancestors,
            csp: (window as PublicLabWindow).__hirewizPublicLab?.csp ?? null };
        }).catch(() => null);
        let diagnosticTimer: ReturnType<typeof setTimeout> | undefined;
        const snapshot = await Promise.race([snapshotPromise, new Promise<null>((resolve) => {
          diagnosticTimer = setTimeout(() => resolve(null), 1000);
        })]).finally(() => { if (diagnosticTimer !== undefined) clearTimeout(diagnosticTimer); });
        await test.info().attach("public-lab-failure-v1", { contentType: "application/json",
          body: Buffer.from(JSON.stringify({ path, width, response_status: responseStatus, errors, snapshot })) });
      } catch { /* Diagnostics must not replace the original failure. */ }
      throw error;
    } finally { await labContext.close(); }
  }
  return { scope: "Disposable CI production Next on loopback; unthrottled Chromium; fresh per-page browser cache; shared warmed server",
    production_proof: false, field_vitals: false, field_inp: null,
    limitations: ["Single sample per path/viewport, not percentile field evidence", "Initial bounded CLS window, not lifetime CLS",
      "Sampled trusted click Event Timing is not INP", "Loopback excludes GCP/Vercel/network/mobile-CPU latency"], measurements };
}

test("fresh candidate: real Chromium → BFF → API → SQL, no AI and reviewed manual handoff", async ({ page, context, baseURL }) => {
  test.skip(!process.env.COLD_BROWSER_FRONTEND_URL, "Run only through the disposable Python orchestrator");
  const external: string[] = [];
  const failures: string[] = [];
  const backendFailures: number[] = [];
  const responses: { path: string; status: number }[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/backend/") || path.startsWith("/api/account/")) {
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
  expect(new URL(baseURL!).protocol).toBe("https:");
  // Authentication remains unavailable through the generic data proxy.
  expect((await request.get(`${baseURL}/api/backend/auth/login`)).status()).toBe(403);
  const providers = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/auth/providers");
  await page.goto("/register");
  await providers; // The real mount effect confirms hydrated event handlers.
  await page.getByLabel("Email address", { exact: true }).fill("candidate.browser@example.com");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-browser-password-123");
  await page.getByRole("checkbox", { name: /I am at least 18/ }).check();
  const registration = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/account/register" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  const registered = await registration;
  expect(registered.status()).toBe(200);
  expect(await registered.json()).toEqual({ status: "REGISTERED" });
  await expect(page).toHaveURL(/\/resume$/);
  const sessionCookies = (await context.cookies()).filter((cookie) => cookie.name === "__Secure-next-auth.session-token");
  expect(sessionCookies).toHaveLength(1);
  expect(sessionCookies[0].secure).toBe(true);
  expect(sessionCookies[0].httpOnly).toBe(true);
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
  const profile = await request.get(`${baseURL}/api/account/profile`);
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
  const pack = page.locator("article").filter({ hasText: "100 job service credits" });
  await expect(pack).toBeVisible();
  const select = pack.getByRole("button", { name: /^(Selected|Review purchase)$/ });
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
    method: "upi", international: false, fee: 2298, tax: 351,
    notes: { hirewiz_order_id: checkout.order_id, sku: "starter_bundle", billing_country: "IN" },
  } } } };
  const raw = JSON.stringify(payload);
  const signature = createHmac("sha256", "synthetic-browser-webhook-secret").update(raw).digest("hex");
  const provider = await requestFactory.newContext({ ignoreHTTPSErrors: true }); // Own loopback TLS, no candidate cookies at the provider boundary.
  try { for (let i = 0; i < 2; i++) {
    const webhook = await provider.post(`${process.env.COLD_BROWSER_BACKEND_URL}/api/billing/webhooks/razorpay`, { data: raw, headers: { "content-type": "application/json", "x-razorpay-signature": signature, "x-razorpay-event-id": "evt_synthetic_browser" } });
    expect(webhook.ok()).toBeTruthy();
  } } finally { await provider.dispose(); }
  await expect(page.getByRole("heading", { name: "Payment confirmed", exact: true })).toBeVisible();
  const paidProfile = await request.get(`${baseURL}/api/account/profile`);
  expect((await paidProfile.json()).ai_credits).toBe(52);
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
  await dialog.getByLabel("Upload a custom PDF, DOCX or TeX source", { exact: true }).setInputFiles(process.env.COLD_BROWSER_CUSTOM!);
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
  const noAiProfile = await request.get(`${baseURL}/api/account/profile`);
  expect((await noAiProfile.json()).ai_credits).toBe(52);
  // Independent second real browser identity must not retrieve the first owner's file/package.
  const outsider = await context.browser()!.newContext({ ignoreHTTPSErrors: true });
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
  // Measure only after the original cold journey and its assertions have completed.
  const publicLab = await measurePublicLab(context.browser()!, baseURL!, guardExternal);
  expect(external).toEqual(["synthetic_checkout_script"]);
  await writeFile(process.env.COLD_BROWSER_BROWSER_PROOF!, JSON.stringify({
    source_bytes_exact: true, custom_bytes_exact: true, original_sha256: createHash("sha256").update(original).digest("hex"),
    custom_sha256: createHash("sha256").update(custom).digest("hex"), cold_user_created_via_ui: true,
    real_nextauth: true, no_bff_interception: true, no_credits_before_webhook: true,
    finite_bundle_sku: "starter_bundle", purchased_analysis_units: 2, analysis_units_after_no_ai_work: 52,
    https_loopback_transport: true, secure_http_only_session_cookie: true,
    public_registration_projection: true, generic_auth_proxy_denied: true,
    stale_approval_rejected: true, cross_owner_denied: true, final_status: final.status,
    search: { desired: 3, delivered: 2, reserved: 6, charged: 4, released: 2 },
    automatic_application_charged: 0, employer_submit_receipt: null, external,
    public_lab: publicLab,
    reviewed_widths: [320, 390, 768, 1024, 1440], axe_violations: 0,
    page_errors: failures.length, server_errors: backendFailures.length, bff_responses: responses,
  }, null, 2) + "\n");
});
