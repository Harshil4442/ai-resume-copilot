const eligibleServices = [{ operation: "resume_tailor", credits: 8, deliverable: "Tailored resume with preserved source layout.", supported_limits: { max_pages: 2 }, source_formats: ["pdf", "docx"], route_version: "reviewed-v1" }];
import { mkdir } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { encode } from "next-auth/jwt";

import type { CreditBundleProduct } from "../lib/billingProducts";
import type { components } from "../lib/generated/api";
import { searchSummary, type EmployerApplication, type EmployerSearch, type JobServiceCatalog } from "../lib/employerJobs";
import type { ServiceCreditHistoryPage } from "../lib/serviceCreditHistory";
import { ownedBootstrapFixture } from "../lib/fixtures/ownedBootstrap";
import { parseOwnedBootstrap } from "../lib/ownedBootstrapContract";

const base = "/v1/employer-jobs";
const now = "2026-10-08T10:00:00Z";
const digest = "a".repeat(64);
const docxType = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const errors = new WeakMap<Page, string[]>();

test.beforeEach(({ page }) => {
  const messages: string[] = [];
  errors.set(page, messages);
  page.on("pageerror", (error) => messages.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !/Failed to load resource: the server responded with a status of (402|409|503)/.test(message.text())) messages.push(message.text());
  });
});
test.afterEach(({ page }) => expect(errors.get(page)).toEqual([]));

function samplePdf(): Buffer {
  const stream = "BT /F1 16 Tf 60 700 Td (Taylor - local review fixture) Tj ET";
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`];
  let text = "%PDF-1.4\n"; const offsets = [0];
  objects.forEach((object, index) => { offsets.push(Buffer.byteLength(text)); text += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(text); text += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(text);
}

function fixture() {
  const catalog: JobServiceCatalog = { enabled: true, auto_submit_enabled: true, search_credits_per_job: 2, apply_credits_per_job: 7, pricing_version: "fixture-price", max_search_jobs: 20, balance: 100, sources: [{ id: "src_fixture", employer: "Example Studio", platform: "greenhouse", careers_url: "https://employer.example/careers", last_success_at: now, status: "healthy", application_mode: "api" }] };
  const posting = { id: "job_fixture", source_id: "src_fixture", external_id: "123", title: "Backend Engineer", employer: "Example Studio", location: "Remote", description: "Build reliable Python and PostgreSQL services.", canonical_url: "https://employer.example/careers/123", apply_url: "https://employer.example/apply/123", platform: "greenhouse", publication_at: null, last_checked_at: now, application_mode: "api" };
  const application: EmployerApplication = { id: "app_fixture", status: "needs_action", application_mode: "api", posting, resume_id: 1, resume_choice: "original", resume_version_id: null, form: { version: "fixture-v1", fields: [{ id: "name", label: "Full name", type: "text", required: true, options: [] }, { id: "work", label: "Work authorization", type: "single_select", required: true, options: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] }], consents: [{ id: "privacy", label: "Employer privacy consent", required: true, policy_url: "https://employer.example/privacy" }] }, package_digest: digest, artifact: { sha256: "b".repeat(64), filename: "Taylor-resume.docx", size_bytes: 1000, media_type: docxType, preview_url: "unused" }, answers: {}, consents: {}, missing_fields: ["name", "work", "privacy"], credit_cost: 7, charged_credits: 0, requires_user_action: true, error: null };
  const state = { searchFailures: 0, prepareFailures: 0, executeOutcome: "confirmed" as EmployerApplication["status"], premium: false, recentPaidPack: false, applicationsVisible: false, badPdf: false, savedResultsUnavailable: false };
  const requests: { path: string; method: string; body: Record<string, unknown>; query: string }[] = [];
  const unexpected: string[] = [];
  const searches: EmployerSearch[] = [];
  const runs: Record<string, unknown>[] = [];
  const opportunity = { id: "opp_existing_fixture", title: "Backend Engineer", company: "Example Studio", location: "Remote", priority: "medium", stage: "saved", source: "manual", source_url: null, compensation: null, deadline_at: null, archived_at: null, created_at: now, updated_at: now, resume_id: 1, latest_match_id: null, latest_analysis_run_id: null, next_action: null, notes: "", outcome: null, outcome_notes: null, outcome_at: null, job_description: posting.description, job_snapshot: {}, activity: [], contacts: [], reminders: [], resume_versions: [] };
  const resumes = [{ id: 1, filename: "Taylor-resume.docx", source_available: true, source_format: "docx", created_at: now }, { id: 2, filename: "Custom-resume.docx", source_available: true, source_format: "docx", created_at: now }];
  const products = ([
    { sku: "starter_bundle", name: "Starter", description: "Finite prepaid pack", amount_minor: 64900, amount_display: "₹649", entitlement_quantity: 100, analysis_units: 2 },
    { sku: "growth_bundle", name: "Growth", description: "Finite prepaid pack", amount_minor: 109900, amount_display: "₹1,099", entitlement_quantity: 300, analysis_units: 6 },
    { sku: "scale_bundle", name: "Scale", description: "Finite prepaid pack", amount_minor: 219900, amount_display: "₹2,199", entitlement_quantity: 700, analysis_units: 15 },
  ] as const).map((p): CreditBundleProduct => ({ ...p, job_service_credits: p.entitlement_quantity, currency: "INR", billing_type: "one_time", duration_days: 0, entitlement_kind: "credit_bundle", auto_renews: false, catalog_visible: true, enabled_for_purchase: true }));
  function billingQuote(sku: string) {
    const product = products.find((item) => item.sku === sku);
    if (!product) throw new Error(`Unexpected billing fixture SKU: ${sku}`);
    return { catalog_version: "fixture-v1", amount_minor: product.amount_minor, currency: product.currency, job_service_credits: product.entitlement_quantity, analysis_units: product.analysis_units };
  }
  function paidBillingOrder() {
    return { order_id: "order_fixture", sku: "growth_bundle", status: "paid", fulfilled: true, amount_minor: 109900, currency: "INR", created_at: now, paid_at: now, refunded_at: null, accepted_quote: billingQuote("growth_bundle") };
  }
  const unifiedProducts = [
    { sku: "starter_pass", name: "Starter", amount_minor: 5900, credits: 40, standard: 7900 },
    { sku: "growth_pass", name: "Growth", amount_minor: 17900, credits: 130, standard: 22900 },
    { sku: "scale_pass", name: "Scale", amount_minor: 44900, credits: 360, standard: 59900 },
  ].map(({ standard, ...product }) => ({ ...product, duration_days: 30, auto_renews: false, purchase_enabled: true, terms_version: "fixture-pass-terms",
    campaign: { id: "fixture-launch", version: "1", starts_at: "2026-10-01T00:00:00Z", ends_at: "2026-10-31T00:00:00Z", timezone: "Asia/Kolkata", status: "active", standard_amount_minor: standard, reference_price_verified: false } }));
  function unifiedQuote(sku: string) {
    const product = unifiedProducts.find((item) => item.sku === sku);
    if (!product) throw new Error(`Unexpected unified fixture SKU: ${sku}`);
    return { ...product, schema_version: "unified-credits-v1", quote_id: `${sku}_quote_fixture`, eligible_services: eligibleServices, catalog_version: "unified-fixture-v1", currency: "INR", created_at: now,
      server_time: now, expires_at: "2026-10-08T10:15:00Z", pass_expires_at: "2026-11-07T10:00:00Z" };
  }

  async function install(context: BrowserContext, baseURL: string) {
    const token = await encode({ secret: process.env.PLAYWRIGHT_AUTH_SECRET || "playwright-secret-at-least-thirty-two-characters", token: { sub: "424242", email: "fixture@example.com", name: "Taylor Example", hirewizUserId: 424242, accessToken: "local-fixture-token" }, maxAge: 3600 });
    await context.addCookies([{ name: "next-auth.session-token", value: token, url: baseURL, httpOnly: true, sameSite: "Lax" }]);
    await context.addInitScript(() => { localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "essential", savedAt: new Date().toISOString() })); });
    await context.route((url) => url.pathname.startsWith("/api/backend/") || /^\/api\/account\/(profile|me|bootstrap)$/.test(url.pathname), async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.replace(/^\/api\/account\/(profile|me|bootstrap)$/, "/auth/$1").replace("/api/backend", "");
      const method = request.method();
      const body = request.headers()["content-type"]?.includes("application/json") ? request.postDataJSON() as Record<string, unknown> : {};
      requests.push({ path, method, body, query: url.searchParams.toString() });
      let value: unknown; let status = 200;
      if (path === "/auth/bootstrap" && method === "GET") {
        value = ownedBootstrapFixture({ profile: { email: "fixture@example.com", tier: state.premium ? "premium" : "free",
          ai_credits: 40, profile_completeness: 80, full_name: "Taylor Example", skills: ["Python"], premium_until: "2026-11-08T10:00:00Z" },
          wallet: { schema_version: "unified-credits-v1", server_time: now, available_credits: 7, reserved_credits: 0, debt_credits: 0, lots: [] } });
        expect(parseOwnedBootstrap(value)).not.toBeNull();
      }
      else if (path === "/auth/profile") value = { email: "fixture@example.com", tier: state.premium ? "premium" : "free", ai_credits: 40, profile_completeness: 80, full_name: "Taylor Example", skills: ["Python"], missing_fields: [], premium_until: "2026-11-08T10:00:00Z" } satisfies components["schemas"]["UserProfileResponse"];
      else if (path === "/auth/me") value = { id: 424242, email: "fixture@example.com", tier: state.premium ? "premium" : "free", ai_credits: 40, job_service_credits: catalog.balance } satisfies components["schemas"]["UserMeResponse"];
      else if (path === "/v1/features") value = { features: { career_workspace: { enabled: true } } };
      else if (path === "/v1/credits/availability") value = { catalog_enabled: false, execution_enabled: false, supported_operations: [] };
      else if (path === "/analytics/summary") value = { profile_completeness: 80, average_match_score: 0, resume_count: 1, applications_count: 0, match_history: [] };
      else if (path === "/v1/reminders") value = [];
      else if (path === "/v1/opportunities" && method === "GET") value = { items: [opportunity], total: 1 };
      else if (path === "/v1/opportunities/opp_existing_fixture") value = opportunity;
      else if (path === "/v1/evidence-items") value = [];
      else if (path === "/v1/analysis-runs" && method === "POST") { const run = { id: `run_fixture_${runs.length}`, operation: body.operation, status: "failed", usage_state: "released", estimated_units: 1, committed_units: 0, error_code: "FixtureError" }; runs.push(run); value = run; }
      else if (path.startsWith("/v1/analysis-runs/run_fixture_")) value = runs.find((run) => run.id === path.split("/").at(-1));
      else if (path === "/resume/list") value = { resumes };
      else if (path === `${base}/catalog`) value = catalog;
      else if (path === `${base}/credits` && method === "GET") value = { balance: catalog.balance, reserved_credits: 0, reservations: [], operations: [], items: [], next_cursor: null } satisfies ServiceCreditHistoryPage;
      else if (path === `${base}/search-summaries` && method === "GET") {
        const offset = Number(url.searchParams.get("cursor")?.replace("history_", "") ?? 0);
        const limit = Number(url.searchParams.get("limit") ?? 10);
        value = { items: searches.slice(offset, offset + limit).map(searchSummary), next_cursor: offset + limit < searches.length ? `history_${offset + limit}` : null };
      }
      else if (path.startsWith(`${base}/searches/`) && path.endsWith("/results") && method === "GET") {
        const saved = searches.find((entry) => entry.id === path.split("/").at(-2));
        if (state.savedResultsUnavailable) { status = 503; value = { detail: "Saved results are temporarily unavailable." }; }
        else if (!saved) { status = 409; value = { detail: "Saved fixture not found." }; }
        else {
          const offset = Number(url.searchParams.get("cursor")?.replace("results_", "") ?? 0);
          const limit = Number(url.searchParams.get("limit") ?? 10);
          value = { search: searchSummary(saved), items: saved.items.slice(offset, offset + limit), total_items: saved.items.length, start_index: offset, next_cursor: offset + limit < saved.items.length ? `results_${offset + limit}` : null, snapshot_version: `saved-search-v1:${digest}` };
        }
      }
      else if (path === `${base}/searches` && method === "GET") value = { items: searches };
      else if (path === `${base}/searches` && method === "POST") {
        if (state.searchFailures-- > 0) { status = 503; value = { detail: "Search request interrupted." }; }
        else { const search: EmployerSearch = { id: "search_fixture", status: "completed", desired_count: Number(body.desired_count), delivered_count: 1, reserved_credits: Number(body.desired_count) * catalog.search_credits_per_job, charged_credits: catalog.search_credits_per_job, refunded_credits: (Number(body.desired_count) - 1) * catalog.search_credits_per_job, created_at: now, query: { resume_id: Number(body.resume_id), role: String(body.role), location: String(body.location), remote_only: Boolean(body.remote_only), published_within_days: body.published_within_days === null ? null : Number(body.published_within_days) }, items: [{ posting, fit: { score: 67, matched_skills: ["Python"], missing_evidence: ["PostgreSQL"], reasons: ["Catalog skills compared locally."], scoring_version: "fixture-rules" }, charged_credits: catalog.search_credits_per_job }] }; searches.push(search); catalog.balance -= search.charged_credits; value = search; }
      } else if (path === `${base}/applications` && method === "GET") value = { items: state.applicationsVisible ? [application] : [] };
      else if (path === `${base}/applications` && method === "POST") { if (state.prepareFailures-- > 0) { status = 503; value = { detail: "Package response interrupted." }; } else { state.applicationsVisible = true; value = application; } }
      else if (path === `${base}/applications/app_fixture/artifact`) { await route.fulfill({ status: 200, contentType: state.badPdf ? "text/html" : application.artifact!.media_type, body: state.badPdf ? "<p>Fixture wrong MIME</p>" : application.artifact!.media_type === "application/pdf" ? samplePdf() : Buffer.from("Local sealed document fixture") }); return; }
      else if (path === `${base}/applications/app_fixture/package`) { application.resume_id = Number(body.resume_id); application.resume_choice = body.resume_choice as EmployerApplication["resume_choice"]; application.resume_version_id = body.resume_version_id as string | null; application.answers = body.answers as EmployerApplication["answers"]; application.consents = body.consents as EmployerApplication["consents"]; application.package_digest = createHash("sha256").update(JSON.stringify(body)).digest("hex"); application.missing_fields = []; application.status = "ready"; value = application; }
      else if (path === `${base}/applications/app_fixture/approve`) { application.status = "approved"; value = application; }
      else if (path === `${base}/applications/app_fixture/execute`) { application.status = state.executeOutcome; application.charged_credits = state.executeOutcome === "confirmed" ? application.credit_cost : 0; application.receipt = state.executeOutcome === "confirmed" ? { receipt_id: "fixture-only-confirmation" } : null; value = application; }
      else if (path === `${base}/applications/app_fixture/cancel`) { application.status = "cancelled"; value = application; }
      else if (path === `${base}/applications/app_fixture`) value = application;
      else if (path === "/v1/resume-versions") value = [{ id: "version_approved", resume_id: 1, opportunity_id: "opp_fixture", version_number: 2, label: "Backend Engineer", approval_state: "approved", structured_content: { format_preservation: "source", source_format: "docx", source_edits: [{ unit_id: "unit_fixture", original_text: "Created reliable services.", replacement_text: "Built reliable services.", evidence_ids: ["evidence_fixture"], reason: "Use a relevant, supported action." }] } }, { id: "version_snapshot", resume_id: 1, opportunity_id: "opp_fixture", version_number: 1, label: "Source snapshot", approval_state: "approved", structured_content: { format_preservation: "source", source_format: "docx", source_edits: [] } }, { id: "version_pending", approval_state: "pending", structured_content: { format_preservation: "source" } }];
      else if (path === "/resume/parse") value = { resume_id: 2, skills: ["Python"], experience_years: 3, sections: {}, extraction_mode: "deterministic", enrichment_state: "not_requested", enrichment_units: 0, warnings: [], source_available: true, source_format: "docx", parsed_json: {} };
      else if (path === "/v1/opportunities" && method === "POST") value = { id: "opp_job_fixture", ...body };
      else if (path === "/public/billing/catalog") value = { catalog_version: "fixture-v1", market: "IN", checkout_enabled: true, provider: "razorpay", products };
      else if (path === "/v1/credits/canary-enrollment" && method === "GET") value = { status: "NOT_AVAILABLE" };
      else if (path === "/v1/credits/catalog") value = { schema_version: "unified-credits-v1", eligible_services: eligibleServices, catalog_version: "unified-fixture-v1", server_time: now, market: "IN", currency: "INR", checkout_enabled: true, products: unifiedProducts };
      else if (path === "/v1/credits/wallet") value = { schema_version: "unified-credits-v1", server_time: now, available_credits: 7, reserved_credits: 0, debt_credits: 0, lots: [] };
      else if (path === "/v1/credits/history") value = { schema_version: "unified-credits-v1", items: [], next_cursor: null };
      else if (path === "/v1/credits/recent-order") value = null;
      else if (path === "/v1/credits/purchase-quotes" && method === "POST") { expect(body.idempotency_key).toEqual(expect.any(String)); value = unifiedQuote(String(body.sku)); }
      else if (path === "/v1/credits/orders" && method === "POST") { expect(body.idempotency_key).toEqual(expect.any(String)); status = 503; value = { detail: { code: "credit_release_unavailable", order_created: false, message: "Fixture checkout disabled. No provider loaded." } }; }
      else if (path === "/v1/usage-events") value = { balance: 40, items: [] };
      else if (path === "/billing/recent-order") value = state.recentPaidPack ? paidBillingOrder() : null;
      else if (path === "/billing/orders/order_fixture" && method === "GET" && state.recentPaidPack) value = paidBillingOrder();
      else if (path === "/billing/orders" && method === "POST") { expect(body.accepted_quote).toEqual(billingQuote(String(body.sku))); expect(body.idempotency_key).toEqual(expect.any(String)); status = 503; value = { detail: "Fixture checkout disabled. No provider loaded." }; }
      else { unexpected.push(`${method} ${path}`); status = 503; value = { detail: "Unexpected fixture request" }; }
      await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    });
  }
  return { catalog, application, state, requests, unexpected, searches, install, billingQuote };
}

async function searchOne(page: Page) {
  await page.goto("/employer-jobs");
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption("1");
  await page.getByLabel("Target role").fill("Backend Engineer");
  await page.getByLabel("Jobs to find").fill("3");
  await page.getByRole("button", { name: "Find jobs", exact: true }).click();
  await expect(page.getByRole("heading", { name: "1 of 3 requested jobs" })).toBeVisible();
}
async function checkLayout(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(result.violations).toEqual([]);
}

test("owned bootstrap fixture serves advisory GET and rejects an unexpected POST", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await page.goto("/employer-jobs");
  await expect(page.getByRole("combobox", { name: "Resume", exact: true })).toBeVisible();
  const response = await page.evaluate(async () => {
    const result = await fetch("/api/account/bootstrap", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    return { status: result.status, body: await result.json() };
  });
  expect(response).toEqual({ status: 503, body: { detail: "Unexpected fixture request" } });
  expect(f.requests.some((request) => request.path === "/auth/bootstrap" && request.method === "GET")).toBe(true);
  expect(f.unexpected).toEqual(["POST /auth/bootstrap"]);
  expect(f.requests.some((request) => request.path.endsWith("/approve") || request.path.endsWith("/execute"))).toBe(false);
});

function seedSavedSearch(f: ReturnType<typeof fixture>) {
  const saved: EmployerSearch = {
    id: "search_saved_pages", status: "completed", desired_count: 27, delivered_count: 27,
    reserved_credits: 54, charged_credits: 54, refunded_credits: 0, created_at: now,
    query: { resume_id: 1, role: "Saved Backend Engineer", location: "Remote", desired_count: 27, remote_only: false, published_within_days: null, excluded_employers: [] },
    scope: { price_quote: { unit_price: 2, maximum_credits: 54, requested_count: 27, pricing_version: "original-fixture-price", search_input_fingerprint: digest } },
    items: Array.from({ length: 27 }, (_, index) => ({
      posting: { ...f.application.posting, id: `job_saved_${index}`, title: `Saved engineer ${index + 1}` },
      fit: { score: 67, matched_skills: ["Python"], missing_evidence: [], reasons: ["Saved deterministic skill comparison."], scoring_version: "fixture-rules" },
      charged_credits: 2,
    })),
  };
  f.searches.push(saved);
  return saved;
}

test("saved result pages keep their original quote and require explicit application preparation", async ({ page, context, baseURL }, testInfo) => {
  const f = fixture(); seedSavedSearch(f); f.catalog.search_credits_per_job = 9;
  await f.install(context, baseURL!); await page.goto("/employer-jobs");
  await page.getByRole("button", { name: /Saved Backend Engineer/ }).click();
  await expect(page.getByText("Showing 1–10 of 27 saved jobs", { exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(10);
  await expect(page.getByText("Saved quote: 2 per new job · up to 54 service credits for 27 requested jobs.", { exact: true })).toBeVisible();
  await expect(page.getByText("54 service credits charged · 0 returned", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next results", exact: true }).click();
  await expect(page.getByText("Showing 11–20 of 27 saved jobs", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Saved engineer 11", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next results", exact: true }).click();
  await expect(page.getByText("Showing 21–27 of 27 saved jobs", { exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(7);
  await expect(page.getByRole("button", { name: "Next results", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Previous results", exact: true }).click();
  await expect(page.getByText("Showing 11–20 of 27 saved jobs", { exact: true })).toBeVisible();
  expect(f.requests.filter((request) => request.method !== "GET")).toEqual([]);
  expect(f.catalog.balance).toBe(100);
  await checkLayout(page);
  await mkdir("../root-saved-search-screenshots", { recursive: true });
  await page.screenshot({ path: `../root-saved-search-screenshots/${testInfo.project.name}-page-2.png`, fullPage: true });
  await page.getByRole("article").filter({ has: page.getByRole("heading", { name: "Saved engineer 11", exact: true }) }).getByRole("button", { name: "Prepare application", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const mutations = f.requests.filter((request) => request.method !== "GET");
  expect(mutations).toHaveLength(1);
  expect(mutations[0].path).toBe(`${base}/applications`);
  expect(mutations[0].body).toMatchObject({ posting_id: "job_saved_10", resume_id: 1, resume_choice: "original" });
  expect(f.requests.some((request) => request.path.endsWith("/approve") || request.path.endsWith("/execute") || request.path === "/v1/analysis-runs")).toBe(false);
  expect(f.unexpected).toEqual([]);
});

test("unavailable saved pages preserve totals and retry without starting paid work", async ({ page, context, baseURL }, testInfo) => {
  const f = fixture(); seedSavedSearch(f); f.state.savedResultsUnavailable = true;
  await f.install(context, baseURL!); await page.goto("/employer-jobs");
  await page.getByRole("button", { name: /Saved Backend Engineer/ }).click();
  await expect(page.getByRole("heading", { name: "27 of 27 requested jobs", exact: true })).toBeVisible();
  await expect(page.getByText("Could not load this saved result page. Your saved search and charges are unchanged.", { exact: true })).toBeVisible();
  await expect(page.getByText("This search saved no jobs.", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Next results", exact: true })).toBeDisabled();
  expect(f.requests.filter((request) => request.method !== "GET")).toEqual([]);
  f.state.savedResultsUnavailable = false;
  await page.getByRole("button", { name: "Retry saved results", exact: true }).click();
  await expect(page.getByText("Showing 1–10 of 27 saved jobs", { exact: true })).toBeVisible();
  await expect(page.getByText("54 service credits charged · 0 returned", { exact: true })).toBeVisible();
  expect(f.requests.filter((request) => request.method !== "GET")).toEqual([]);
  expect(f.catalog.balance).toBe(100);
  await checkLayout(page); expect(f.unexpected).toEqual([]);
  await mkdir("../root-saved-search-screenshots", { recursive: true });
  await page.screenshot({ path: `../root-saved-search-screenshots/${testInfo.project.name}-retried-page-1.png`, fullPage: true });
});

test("account switching clears the previous search, resume cache, and reviewed approval package", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!);
  let owner = "a";
  const nextOwnerRequests: { path: string; method: string }[] = [];
  await context.route("**/api/auth/session", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { id: owner === "a" ? "424242" : "424243", email: `${owner}@fixture.example`, name: `Owner ${owner}` }, expires: "2099-01-01T00:00:00.000Z" }) });
  });
  await context.route((url) => url.pathname.startsWith("/api/backend/") || /^\/api\/account\/(profile|bootstrap)$/.test(url.pathname), async (route) => {
    if (owner === "a") { await route.fallback(); return; }
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/account\/(profile|bootstrap)$/, "/auth/$1").replace("/api/backend", "");
    nextOwnerRequests.push({ path, method: route.request().method() });
    let value: unknown;
    if (path === "/auth/bootstrap" && route.request().method() === "GET") {
      value = ownedBootstrapFixture({ profile: { email: "b@fixture.example", ai_credits: 9 }, features: { career_workspace: false } });
      expect(parseOwnedBootstrap(value)).not.toBeNull();
    }
    else if (path === "/resume/list") value = { resumes: [{ id: 3, filename: "New-owner-resume.docx", source_available: true, source_format: "docx", created_at: now }] };
    else if (path === `${base}/catalog`) value = { ...f.catalog, balance: 37, sources: [] };
    else if (path === `${base}/searches` || path === `${base}/applications`) value = { items: [] };
    else if (path === `${base}/search-summaries`) value = { items: [], next_cursor: null };
    else if (path === "/v1/features") value = { features: { career_workspace: { enabled: false } } };
    else if (path === "/v1/credits/availability") value = { catalog_enabled: false, execution_enabled: false, supported_operations: [] };
    else if (path === "/auth/profile") value = { email: "b@fixture.example", tier: "free", ai_credits: 9, job_service_credits: 37 };
    else { await route.abort(); return; }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
  });
  await searchOne(page);
  await page.getByRole("button", { name: "Prepare application", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Full name (required)").fill("Taylor Example");
  await dialog.getByLabel("Work authorization (required)").selectOption("yes");
  await dialog.getByRole("checkbox", { name: "Employer privacy consent" }).check();
  await dialog.getByRole("button", { name: "Save and refresh review" }).click();
  await dialog.getByRole("checkbox", { name: /I reviewed this employer/ }).check();
  await expect(dialog.getByRole("button", { name: "Approve and submit" })).toBeEnabled();

  // Mimic a different account signing in in another tab, then NextAuth's normal
  // session broadcast refresh. No reload hides a retained component/cache bug.
  owner = "b";
  const token = await encode({ secret: process.env.PLAYWRIGHT_AUTH_SECRET || "playwright-secret-at-least-thirty-two-characters", token: { sub: "424243", email: "b@fixture.example", hirewizUserId: 424243, accessToken: "second-local-fixture-token" }, maxAge: 3600 });
  await context.addCookies([{ name: "next-auth.session-token", value: token, url: baseURL!, httpOnly: true, sameSite: "Lax" }]);
  await page.evaluate(() => window.dispatchEvent(new StorageEvent("storage", { key: "nextauth.message", newValue: JSON.stringify({ event: "session", data: { trigger: "signIn" } }) })));

  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "1 of 3 requested jobs" })).toHaveCount(0);
  const resume = page.getByRole("combobox", { name: "Resume", exact: true });
  await expect(resume.locator("option[value='3']")).toHaveText("New-owner-resume.docx · #3");
  await expect(resume.locator("option[value='1']")).toHaveCount(0);
  await expect(resume).toHaveValue("");
  await expect(page.getByLabel("Target role")).toHaveValue("");
  await expect(page.getByText("37", { exact: true })).toBeVisible();
  expect(nextOwnerRequests.some((r) => r.path === "/resume/list")).toBe(true);
  expect(nextOwnerRequests.some((r) => r.path === "/auth/bootstrap" && r.method === "GET")).toBe(true);
  expect(nextOwnerRequests.some((r) => r.path === `${base}/catalog`)).toBe(true);
  expect(nextOwnerRequests.some((r) => r.path.includes("app_fixture") || r.method !== "GET")).toBe(false);
  expect(f.requests.some((r) => r.path.endsWith("/approve") || r.path.endsWith("/execute"))).toBe(false);
  await checkLayout(page); expect(f.unexpected).toEqual([]);
});

for (const [kind, label] of [["release_or_republication", "Released or republished"], ["publication", "Published"]]) {
  test(`job date labels distinguish ${kind} from an original publication`, async ({ page, context, baseURL }) => {
    const f = fixture(); f.application.posting.publication_at = now; f.application.posting.publication_kind = kind;
    await f.install(context, baseURL!); await searchOne(page);
    await expect(page.getByLabel("Published/released within")).toHaveValue("");
    await expect(page.getByText(/Dates may reflect republishing/)).toBeVisible();
    await expect(page.getByText(new RegExp(`^${label} `))).toBeVisible();
    if (kind === "release_or_republication") await expect(page.getByText(/^Published /)).toHaveCount(0);
    await checkLayout(page); expect(f.unexpected).toEqual([]);
  });
}

test("server prices, paid count, partial refund, and idempotent interrupted search", async ({ page, context, baseURL }) => {
  const f = fixture(); f.state.searchFailures = 1; await f.install(context, baseURL!);
  await page.goto("/employer-jobs");
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption("1"); await page.getByLabel("Target role").fill("Backend Engineer"); await page.getByLabel("Jobs to find").fill("3");
  await expect(page.getByText("Up to 6 service credits", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Find jobs", exact: true }).click(); await expect(page.getByRole("alert").filter({ hasText: "Search request interrupted" })).toContainText("Search request interrupted");
  await page.getByRole("button", { name: "Find jobs", exact: true }).click(); await expect(page.getByRole("heading", { name: "1 of 3 requested jobs" })).toBeVisible();
  const posted = f.requests.filter((r) => r.path === `${base}/searches` && r.method === "POST"); expect(posted).toHaveLength(2); expect(posted[0].body).toEqual(posted[1].body);
  await expect(page.getByText("2 service credits charged · 4 returned")).toBeVisible(); await expect(page.getByText("Publication date unknown")).toBeVisible(); expect(f.requests.some((r) => r.path.endsWith("/execute"))).toBe(false); await checkLayout(page); expect(f.unexpected).toEqual([]);
});

test("exact package review, explicit consent, changes clear approval, server-confirmed submission", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await searchOne(page); await page.getByRole("button", { name: "Prepare application", exact: true }).click();
  const dialog = page.getByRole("dialog"); await expect(dialog.getByRole("button", { name: "Approve and submit" })).toBeDisabled(); await expect(dialog.getByRole("checkbox", { name: "Employer privacy consent" })).not.toBeChecked();
  await dialog.getByLabel("Full name (required)").fill("Taylor Example"); await dialog.getByLabel("Work authorization (required)").selectOption("yes"); await dialog.getByRole("checkbox", { name: "Employer privacy consent" }).check();
  await dialog.getByRole("button", { name: "Save and refresh review" }).click(); await expect(dialog.getByText("Exact sealed application file")).toBeVisible();
  await dialog.getByRole("checkbox", { name: /I reviewed this employer/ }).check(); await dialog.getByLabel("Full name (required)").fill("Taylor Updated"); await expect(dialog.getByRole("checkbox", { name: /I reviewed this employer/ })).not.toBeChecked(); await expect(dialog.getByRole("button", { name: "Approve and submit" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Save and refresh review" }).click(); await dialog.getByRole("checkbox", { name: /I reviewed this employer/ }).check(); await checkLayout(page); await dialog.getByRole("button", { name: "Approve and submit" }).click();
  await expect(dialog.getByRole("heading", { name: "Employer submission confirmed" })).toBeVisible(); await expect(dialog.getByText("7 service credits charged.")).toBeVisible();
  const approval = f.requests.find((r) => r.path.endsWith("/approve"))!; const execute = f.requests.find((r) => r.path.endsWith("/execute"))!; expect(approval.body).toEqual({ package_digest: f.application.package_digest, allowed_actions: ["fill", "upload", "submit"] }); expect(execute.body).toEqual({ package_digest: approval.body.package_digest }); expect(f.requests.findIndex((r) => r === approval)).toBeLessThan(f.requests.findIndex((r) => r === execute)); expect(f.unexpected).toEqual([]);
});

test("tailored or custom file choices are saved explicitly before use", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await searchOne(page); await page.getByRole("button", { name: "Prepare application", exact: true }).click(); const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox", { name: "Resume choice", exact: true }).selectOption("tailored"); await dialog.getByRole("combobox", { name: "Approved version", exact: true }).selectOption("version_approved"); await expect(dialog.locator("option[value=version_pending]")).toHaveCount(0); await expect(dialog.locator("option[value=version_snapshot]")).toHaveCount(0); await dialog.getByRole("button", { name: "Save and refresh review" }).click(); await expect(dialog.getByText("Exact sealed application file")).toBeVisible();
  expect(f.requests.filter((r) => r.method === "PUT").at(-1)?.body).toMatchObject({ resume_choice: "tailored", resume_version_id: "version_approved" });
  await dialog.getByRole("combobox", { name: "Resume choice", exact: true }).selectOption("custom"); await dialog.getByLabel("Upload a custom PDF, DOCX or TeX source").setInputFiles({ name: "custom.docx", mimeType: docxType, buffer: Buffer.from("Local custom file fixture") }); await expect(dialog.getByText("Your saved preview is out of date.", { exact: false })).toBeVisible(); await dialog.getByRole("button", { name: "Save and refresh review" }).click();
  expect(f.requests.filter((r) => r.method === "PUT").at(-1)?.body).toMatchObject({ resume_choice: "custom", resume_id: 2, resume_version_id: null }); expect(f.requests.some((r) => r.path.endsWith("/approve"))).toBe(false); expect(f.unexpected).toEqual([]);
});

test("manual handoff never claims submission and unknown outcome cannot be resent", async ({ page, context, baseURL }) => {
  const f = fixture(); f.state.applicationsVisible = true; f.application.application_mode = "manual"; f.application.status = "manual_handoff"; await f.install(context, baseURL!); await page.goto("/employer-jobs"); await page.getByRole("button", { name: /Backend Engineer Example Studio/ }).click(); const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("link", { name: "Open employer application" })).toHaveAttribute("href", "https://employer.example/apply/123"); await expect(dialog.getByText(/Preparation is not a submission/)).toBeVisible(); await expect(dialog.getByRole("button", { name: "Approve and submit" })).toHaveCount(0); await dialog.getByRole("button", { name: "Close application review" }).click();
  f.application.application_mode = "api"; f.application.status = "unknown"; await page.reload(); await page.getByRole("button", { name: /Backend Engineer Example Studio/ }).click(); await expect(dialog.getByRole("alert")).toContainText("Do not send another copy"); await expect(dialog.getByRole("button", { name: "Approve and submit" })).toHaveCount(0); await expect(dialog.getByRole("button", { name: "Cancel remaining work" })).toBeDisabled(); await checkLayout(page); expect(f.requests.some((r) => r.path.endsWith("/execute"))).toBe(false); expect(f.unexpected).toEqual([]);
});

test("unavailable service or insufficient balance cannot start a paid search", async ({ page, context, baseURL }) => {
  const f = fixture(); f.catalog.balance = 0; await f.install(context, baseURL!); await page.goto("/employer-jobs"); await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption("1"); await page.getByLabel("Target role").fill("Backend Engineer"); await expect(page.getByRole("button", { name: "Find jobs", exact: true })).toBeDisabled(); await expect(page.getByText("You need 20 service credits for this search.", { exact: false })).toBeVisible(); f.catalog.balance = 100; f.catalog.enabled = false; await page.reload(); await expect(page.getByText(/Employer search is not enabled yet/)).toBeVisible(); await expect(page.getByRole("button", { name: "Find jobs", exact: true })).toBeDisabled(); expect(f.requests.some((r) => r.method === "POST")).toBe(false); expect(f.unexpected).toEqual([]);
});

test("legacy access keeps purchased terms while new passes require reviewed quotes", async ({ page, context, baseURL }) => {
  const f = fixture(); f.state.premium = true; await f.install(context, baseURL!); await page.goto("/billing");
  await page.getByRole("button", { name: "Review Growth pass", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Review your Growth pass" })).toBeVisible();
  await page.getByRole("checkbox", { name: /I confirm my billing country/ }).check();
  await page.getByRole("button", { name: "Continue with ₹179 payment" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Fixture checkout disabled" })).toBeVisible();
  expect(f.requests.find((r) => r.path === "/v1/credits/orders")?.body).toEqual({ quote_id: "growth_pass_quote_fixture", billing_country: "IN", idempotency_key: expect.any(String) });
  await expect(page.getByRole("heading", { name: "Credit payment confirmed" })).toHaveCount(0);
  f.state.recentPaidPack = true; await page.reload();
  await expect(page.getByRole("heading", { name: "Payment confirmed", exact: true })).toBeVisible();
  await expect(page.getByText("Payment confirmed. Your original purchased entitlements were delivered.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Review Growth pass" })).toBeEnabled();
  await checkLayout(page); expect(f.unexpected).toEqual([]);
});

test("service credit checkout links preserve explicit pass review before purchase", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await page.goto("/employer-jobs");
  const buy = page.getByRole("link", { name: "Buy credits", exact: true });
  await expect(buy).toHaveAttribute("href", "/billing"); await buy.click(); await expect(page).toHaveURL(/\/billing$/);
  await expect(page.getByRole("button", { name: "Review Starter pass" })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Reviewed service pilot", exact: true })).toHaveCount(0);
  expect(f.requests.some((r) => r.path === "/v1/credits/orders" || r.path === "/v1/credits/purchase-quotes")).toBe(false);
  await page.getByRole("button", { name: "Review Starter pass" }).click();
  await expect(page.getByRole("button", { name: "Continue with ₹59 payment" })).toBeDisabled();
  await page.getByRole("checkbox", { name: /I confirm my billing country/ }).check();
  await page.getByRole("button", { name: "Continue with ₹59 payment" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Fixture checkout disabled" })).toBeVisible();
  expect(f.requests.find((r) => r.path === "/v1/credits/orders")?.body).toEqual({ quote_id: "starter_pass_quote_fixture", billing_country: "IN", idempotency_key: expect.any(String) });
  expect(f.requests.some((r) => r.path === "/billing/orders")).toBe(false);
  await checkLayout(page); expect(f.unexpected).toEqual([]);
});

test("billing links keep manual choice and ignore an unsupported product", async ({ page, context, baseURL }) => {
  const f = fixture(); await f.install(context, baseURL!); await page.goto("/billing?sku=growth_pass");
  await expect(page.getByText("Review your selected Growth pass above to get its current quote.")).toBeVisible();
  expect(f.requests.some((r) => r.method === "POST")).toBe(false);
  await page.getByRole("button", { name: "Review Starter pass" }).click();
  await expect(page.getByRole("heading", { name: "Review your Starter pass" })).toBeVisible();
  await page.goto("/billing?sku=unsupported_product");
  await expect(page.getByRole("button", { name: "Review Starter pass" })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Review your Starter pass" })).toHaveCount(0);
  expect(f.requests.some((r) => r.path === "/v1/credits/orders" || r.path === "/billing/orders")).toBe(false);
  await checkLayout(page); expect(f.unexpected).toEqual([]);
});

test("exactly three finite passes disclose one balance without invented reference prices", async ({ page, context, baseURL }) => {
  const f = fixture(); f.catalog.balance = 3; await f.install(context, baseURL!); await page.goto("/billing");
  const legacyCredits = page.getByRole("region", { name: "Current entitlement" }).getByText("Job service credits", { exact: true }).locator("..");
  await expect(legacyCredits.getByText("3", { exact: true })).toBeVisible();
  await expect(page.getByText("Available credits", { exact: true }).locator("..")).toContainText("7");
  await expect(page.getByRole("article")).toHaveCount(3); await expect(page.locator("s")).toHaveCount(0);
  for (const [name, credits, total] of [["Starter",40,"₹59"],["Growth",130,"₹179"],["Scale",360,"₹449"]] as const) {
    const pack = page.getByRole("article").filter({ has: page.getByRole("heading", { name, exact: true }) });
    await expect(pack.getByText(`${credits} credits`, { exact: true })).toBeVisible();
    await expect(pack.getByText(total, { exact: true })).toBeVisible();
    await expect(pack.getByText("Valid for 30 days from verified payment")).toBeVisible();
  }
  await page.getByRole("button", { name: "Review Scale pass" }).click();
  await expect(page.getByRole("heading", { name: "Review your Scale pass" })).toBeVisible();
  await expect(page.getByText("360 credits · 30 days · no automatic renewal")).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with ₹449 payment" })).toBeDisabled();
  expect(f.requests.some((r) => r.path === "/v1/credits/orders" || r.path === "/billing/orders")).toBe(false);
  await checkLayout(page); expect(f.unexpected).toEqual([]);
});

test("exact PDF MIME failures block approval and allow a safe preview retry", async ({ page, context, baseURL }) => {
  const f = fixture(); f.state.applicationsVisible = true; f.state.badPdf = true; f.application.status = "ready"; f.application.missing_fields = []; f.application.artifact!.media_type = "application/pdf"; f.application.artifact!.filename = "Taylor.pdf"; await f.install(context, baseURL!);
  await page.goto("/employer-jobs"); await page.getByRole("button", { name: /Backend Engineer Example Studio/ }).click(); const dialog = page.getByRole("dialog"); await expect(dialog.getByRole("alert")).toContainText("did not return a PDF"); await expect(dialog.getByRole("checkbox", { name: /I reviewed this employer/ })).toBeDisabled();
  f.state.badPdf = false; await dialog.getByRole("button", { name: "Retry exact file preview" }).click(); await expect(dialog.locator("iframe[title='Exact resume for this application']")).toHaveAttribute("src", /^blob:/); await expect(dialog.getByRole("checkbox", { name: /I reviewed this employer/ })).toBeEnabled(); await checkLayout(page); expect(f.requests.some((r) => r.path.endsWith("/approve"))).toBe(false); expect(f.unexpected).toEqual([]);
});

test("queued application polling reaches confirmation and refreshes paid status", async ({ page, context, baseURL }) => {
  await page.clock.install();
  const f = fixture(); f.state.applicationsVisible = true; f.state.executeOutcome = "queued"; f.application.status = "ready"; f.application.missing_fields = []; await f.install(context, baseURL!); await page.goto("/employer-jobs"); await page.getByRole("button", { name: /Backend Engineer Example Studio/ }).click(); const dialog = page.getByRole("dialog"); await dialog.getByRole("checkbox", { name: /I reviewed this employer/ }).check(); await dialog.getByRole("button", { name: "Approve and submit" }).click(); await expect(dialog.getByRole("status")).toContainText("Waiting for an employer-confirmed result");
  f.application.status = "confirmed"; f.application.charged_credits = 7; f.catalog.balance = 93;
  const confirmation = page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname.endsWith("/applications/app_fixture"));
  // Advance the bounded polling clock only after the real queued response/UI.
  await page.clock.fastForward(30_000);
  await confirmation;
  await expect(dialog.getByRole("heading", { name: "Employer submission confirmed" })).toBeVisible(); await dialog.getByRole("button", { name: "Close application review" }).click(); await expect(page.getByRole("button", { name: /Backend Engineer Example Studio Submission confirmed/ })).toBeVisible(); await expect(page.getByRole("main").getByText("93", { exact: true })).toBeVisible(); expect(f.requests.filter((r) => r.path.endsWith("/execute"))).toHaveLength(1); expect(f.unexpected).toEqual([]);
});


test("existing dashboard, workspace, resume and billing remain usable at every width", async ({ page, context, baseURL }, testInfo) => {
  const f = fixture(); await f.install(context, baseURL!);
  if (process.env.PLAYWRIGHT_LAB_DIR) await page.addInitScript(() => {
    const metrics = { lcp_ms: 0, cls: 0, shifts: [] as Record<string, unknown>[] };
    (window as unknown as Window & { hirewizLab: typeof metrics }).hirewizLab = metrics;
    new PerformanceObserver((entries) => { for (const entry of entries.getEntries()) metrics.lcp_ms = entry.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((entries) => { for (const entry of entries.getEntries()) { const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean; sources: { node?: Element; previousRect: DOMRectReadOnly; currentRect: DOMRectReadOnly }[] }; if (!shift.hadRecentInput) { metrics.cls += shift.value; metrics.shifts.push({ value: shift.value, nodes: shift.sources?.map((source) => ({ tag: source.node?.nodeName, className: source.node instanceof Element ? source.node.getAttribute("class") : null, from: source.previousRect.toJSON(), to: source.currentRect.toJSON() })) }); } } }).observe({ type: "layout-shift", buffered: true });
  });
  const lab: Record<string, unknown>[] = [];
  for (const [path, heading] of [["/employer-jobs", "Find your next role. Apply with control."], ["/dashboard", "Good to see you, Taylor."], ["/workspace", "Your opportunities"], ["/workspace/opp_existing_fixture", "Backend Engineer"], ["/resume", "Add your source resume"], ["/billing", "Credits for your next move"]]) {
    await page.goto(path); await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible(); await expect(page.locator(".animate-pulse")).toHaveCount(0); await page.evaluate(() => document.fonts.ready); await checkLayout(page);
    if (process.env.PLAYWRIGHT_LAB_DIR) lab.push({ path, ...await page.evaluate(async () => {
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming;
      const scripts = performance.getEntriesByType("resource").filter((entry) => (entry as PerformanceResourceTiming).initiatorType === "script") as PerformanceResourceTiming[];
      return { ...(window as unknown as Window & { hirewizLab: { lcp_ms: number; cls: number } }).hirewizLab, ttfb_ms: navigation.responseStart, script_transfer_bytes: scripts.reduce((sum, entry) => sum + entry.transferSize, 0), script_count: scripts.length };
    }) });
  }
  if (process.env.PLAYWRIGHT_LAB_DIR) { await mkdir(process.env.PLAYWRIGHT_LAB_DIR, { recursive: true }); await testInfo.attach("local-production-lab", { body: JSON.stringify(lab), contentType: "application/json" }); const { writeFile } = await import("node:fs/promises"); await writeFile(`${process.env.PLAYWRIGHT_LAB_DIR}/${testInfo.project.name}.json`, JSON.stringify({ fixture_backend: true, network_throttling: false, cpu_throttling: false, width: page.viewportSize()?.width, measurements: lab }, null, 2)); }
  await page.goto("/workspace/opp_existing_fixture");
  await expect(page.getByRole("combobox", { name: /Match mode/ })).toHaveValue("basic");
  await page.getByRole("button", { name: "Run match", exact: true }).first().click(); await expect(page.getByText("Analysis did not complete.", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: /Match mode/ }).selectOption("enhanced"); await page.getByRole("button", { name: "Run match", exact: true }).first().click(); await expect.poll(() => f.requests.filter((request) => request.path === "/v1/analysis-runs").length).toBe(2);
  await page.getByRole("button", { name: "Interview", exact: true }).click(); await expect(page.getByRole("combobox", { name: /Question mode/ })).toHaveValue("curated"); await page.getByRole("button", { name: "Generate questions", exact: true }).click(); await expect.poll(() => f.requests.filter((request) => request.path === "/v1/analysis-runs").length).toBe(3); await expect(page.getByRole("button", { name: "Generate questions", exact: true })).toBeEnabled(); await page.getByRole("combobox", { name: /Question mode/ }).selectOption("enhanced"); await page.getByRole("button", { name: "Generate questions", exact: true }).click(); await expect.poll(() => f.requests.filter((request) => request.path === "/v1/analysis-runs").length).toBe(4); await checkLayout(page);
  expect(f.requests.filter((request) => request.path === "/v1/analysis-runs").map((request) => (request.body.input as { mode: string }).mode)).toEqual(["basic", "enhanced", "curated", "enhanced"]);
  await page.goto("/resume"); await expect(page.getByRole("checkbox", { name: /Optional AI skill enrichment/ })).not.toBeChecked(); await page.locator("input[type=file]").setInputFiles({ name: "resume.docx", mimeType: docxType, buffer: Buffer.from("Local upload fixture") }); await page.getByRole("button", { name: "Parse resume", exact: false }).click(); await expect(page.getByText("Deterministic parsing · no generative AI")).toBeVisible();
  if (process.env.PLAYWRIGHT_SCREENSHOT_DIR) { await mkdir(process.env.PLAYWRIGHT_SCREENSHOT_DIR, { recursive: true }); await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" })); await page.screenshot({ path: `${process.env.PLAYWRIGHT_SCREENSHOT_DIR}/resume-${testInfo.project.name}.png`, fullPage: true }); await searchOne(page); await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" })); await page.screenshot({ path: `${process.env.PLAYWRIGHT_SCREENSHOT_DIR}/jobs-${testInfo.project.name}.png`, fullPage: true }); }
  expect(f.unexpected).toEqual([]);
});


test("cancelling queued work locks edits and prevents another execute", async ({ page, context, baseURL }) => {
  const f = fixture(); f.state.applicationsVisible = true; f.application.status = "queued"; f.application.missing_fields = []; await f.install(context, baseURL!); await page.goto("/employer-jobs"); await page.getByRole("button", { name: /Backend Engineer Example Studio/ }).click(); const dialog = page.getByRole("dialog"); await expect(dialog.getByRole("combobox", { name: "Resume choice", exact: true })).toBeDisabled(); await dialog.getByRole("button", { name: "Cancel remaining work" }).click(); await expect(dialog.getByText("Cancelled", { exact: true })).toBeVisible(); await expect(dialog.getByRole("button", { name: "Approve and submit" })).toHaveCount(0); expect(f.requests.filter((r) => r.path.endsWith("/cancel"))).toHaveLength(1); expect(f.requests.some((r) => r.path.endsWith("/execute"))).toBe(false); expect(f.unexpected).toEqual([]);
});

test("reviewed discovery purchases one bounded batch and preserves legacy history", async ({ page, context, baseURL }) => {
  const f = fixture(); seedSavedSearch(f); await f.install(context, baseURL!);
  const discoveryRequests: { path: string; method: string; body: Record<string, unknown> }[] = [];
  const item = { posting: f.application.posting, fit: { score: 67, matched_skills: ["Python"], missing_evidence: ["PostgreSQL"], reasons: ["Documented skill overlap"], scoring_version: "native-v1" }, charged_credits: 0, included_in_batch: true, billing_currency: "credits" };
  const batch = { id: "search_unified", status: "completed", billing_currency: "credits", desired_count: 5, delivered_count: 2, charged_credits: 1, reserved_credits: 0, refunded_credits: 0, partial_batch: true, maximum_batch_size: 5, scope: {}, items: [item, { ...item, posting: { ...item.posting, id: "job_second", title: "Platform Engineer" } }] };
  await context.route((url) => url.pathname === "/api/backend/v1/credits/availability" || url.pathname.startsWith("/api/backend/v1/credits/discovery-"), async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const body = request.method() === "POST" ? request.postDataJSON() : {};
    discoveryRequests.push({ path, method: request.method(), body });
    const value = path.endsWith("/availability") ? { catalog_enabled: true, execution_enabled: true, supported_operations: ["job_discovery_batch"] }
      : path.endsWith("/discovery-quotes") ? { schema_version: "unified-service-quote-v1", quote_id: "csq_discovery", operation: "job_discovery_batch", input_fingerprint: digest, route_id: "native-discovery", route_version: "1", mode: "native", tariff_version: "v1", credits: 1, deliverable: "Up to 5 new verified employer jobs.", supported_limits: { maximum_results: 5, candidate_limit: 1000 }, created_at: now, server_time: now, expires_at: "2026-10-08T10:15:00Z" } : batch;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/employer-jobs");
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption("1");
  await page.getByLabel("Target role").fill("Backend Engineer");
  await expect(page.getByLabel("Jobs to find", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Review one-credit search" }).click();
  await expect(page.getByText(/A completed batch with 1–4 new verified jobs still costs one credit/)).toBeVisible();
  expect(discoveryRequests.filter((request) => request.path.endsWith("/discovery-batches"))).toHaveLength(0);
  await checkLayout(page);
  await page.getByRole("button", { name: "Approve 1 credit", exact: true }).click();
  await expect(page.getByText("1 credit charged · 0 held · 0 restored", { exact: true })).toBeVisible();
  await expect(page.getByText("Partial batch: 2 of 5 requested jobs. The reviewed one-credit batch charge applies.", { exact: true })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(2); await checkLayout(page);
  await page.getByRole("button", { name: /Saved Backend Engineer/ }).click();
  await expect(page.getByText("54 service credits charged · 0 returned", { exact: true })).toBeVisible();
  expect(discoveryRequests.filter((request) => request.path.endsWith("/discovery-batches") && request.method === "POST")).toHaveLength(1);
  expect(f.requests.some((request) => request.path === `${base}/searches` && request.method === "POST")).toBe(false);
  expect(f.requests.some((request) => request.path === `${base}/applications` && request.method === "POST")).toBe(false);
  expect(f.unexpected).toEqual([]);
});
