import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { encode } from "next-auth/jwt";
import type { EmployerSearch } from "../lib/employerJobs";

// UI integration only: API responses are explicit synthetic fixtures. Actual
// search/ledger/PG behaviour is independently exercised by backend tests.
test("preferences bind paid search retries, restore saved choices and remain usable at every viewport", async ({ page, context, baseURL }, testInfo) => {
  const root = new URL(baseURL!);
  const token = await encode({ token: { sub: "1", hirewizUserId: 1, email: "fixture@example.com", name: "Fixture", accessToken: "fixture-ui-only-bearer" }, secret: "playwright-secret-at-least-thirty-two-characters", maxAge: 3600 });
  await context.addCookies([{ name: "next-auth.session-token", value: token, domain: root.hostname, path: "/", httpOnly: true, sameSite: "Lax", secure: false }]);
  await context.addInitScript(() => { localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "essential", savedAt: new Date().toISOString() })); });
  const requests: Record<string, unknown>[] = [], searches: EmployerSearch[] = [];
  let interrupted = true;
  const unexpected: string[] = [];
  await page.route((url) => url.pathname.startsWith("/api/backend/") || url.pathname === "/api/account/profile", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^\/api\/account\/profile$/, "/auth/profile").replace("/api/backend", "");
    const method = request.method();
    let value: unknown, status = 200;
    if (path === "/auth/profile") value = { email: "fixture@example.com", tier: "premium", ai_credits: 500, job_service_credits: 100, profile_completeness: 80, full_name: "Fixture" };
    else if (path === "/v1/features") value = { features: { career_workspace: { enabled: true } } };
    else if (path === "/resume/list") value = { resumes: [{ id: 10, filename: "Fixture.pdf", source_available: true, source_format: "pdf" }] };
    else if (path === "/v1/employer-jobs/catalog") value = { enabled: true, auto_submit_enabled: false, search_credits_per_job: 2, apply_credits_per_job: 7, max_search_jobs: 20, balance: 100, sources: [] };
    else if (path === "/v1/employer-jobs/applications") value = { items: [] };
    else if (path === "/v1/employer-jobs/searches" && method === "GET") value = { items: searches };
    else if (path === "/v1/employer-jobs/searches" && method === "POST") {
      const body = request.postDataJSON(); requests.push(body);
      if (interrupted) { interrupted = false; status = 503; value = { detail: "Fixture interrupted response" }; }
      else {
        const result: EmployerSearch = { id: "search-preference-fixture", status: "completed", desired_count: body.desired_count, delivered_count: 1, reserved_credits: 6, charged_credits: 2, refunded_credits: 4, created_at: new Date().toISOString(), query: body, scope: { known_preference_conflicts: 1 }, items: [{ posting: { id: "posting-preference-fixture", source_id: "fixture", external_id: "123", title: "Senior Python Software Developer", employer: "Fixture", location: "Bengaluru", description: "Python services", canonical_url: "https://employer.example/job/123", apply_url: "https://employer.example/job/123", platform: "lever", publication_at: null, last_checked_at: new Date().toISOString(), application_mode: "manual" }, fit: { score: 67, matched_skills: ["Python"], missing_evidence: [], reasons: ["Rule-based overlap"], scoring_version: "fixture" }, charged_credits: 2, preference_evaluation: { version: "employer-preferences-v1", eligible: true, states: { sponsorship: "unknown", country_codes: "match" }, messages: ["sponsorship: employer evidence unavailable; review the posting"], eligibility_verified: false } }] };
        searches.push(result); value = result;
      }
    } else { unexpected.push(`${method} ${path}`); value = {}; }
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/employer-jobs");
  await page.getByRole("combobox", { name: "Resume", exact: true }).selectOption("10");
  await page.getByLabel("Target role").fill("Senior Python SDE");
  await page.getByLabel("Jobs to find").fill("3");
  await page.getByText("More search preferences", { exact: true }).click();
  await page.getByLabel(/Country codes/).fill("IN");
  await page.getByLabel(/Posting language codes/).fill("en");
  await page.getByLabel("Full-time", { exact: true }).check();
  await page.getByLabel("Salary minimum", { exact: true }).fill("200");
  await page.getByLabel("Salary maximum", { exact: true }).fill("100");
  await expect(page.getByRole("button", { name: "Find jobs", exact: true })).toBeDisabled();
  await expect(page.getByRole("alert").filter({ hasText: "Salary minimum cannot exceed maximum." })).toContainText("Salary minimum cannot exceed maximum.");
  await page.getByLabel("Salary maximum", { exact: true }).fill("300");
  await page.getByRole("combobox", { name: "Do you need sponsorship?", exact: true }).selectOption("yes");
  await page.getByLabel("Countries where you have work authorization").fill("IN");
  await page.getByRole("combobox", { name: "Are you willing to relocate?", exact: true }).selectOption("no");
  await page.getByRole("button", { name: "Find jobs", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "interrupted" })).toContainText("interrupted");
  await page.getByRole("button", { name: "Find jobs", exact: true }).click();
  await expect(page.getByRole("heading", { name: "1 of 3 requested jobs" })).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
  expect(requests[0].preferences).toMatchObject({ version: 1, country_codes: ["IN"], employment_types: ["full_time"], salary: { minimum: "200", maximum: "300", currency: "INR", period: "year" }, sponsorship_required: true, authorized_country_codes: ["IN"], willing_to_relocate: false, unknown_metadata: "include" });
  await expect(page.getByText("Preferences to review", { exact: true })).toBeVisible();
  await expect(page.getByText(/Employer facts match these choices/)).toContainText("country codes");
  await expect(page.getByText(/known preference conflicts excluded/)).toContainText("1 known preference conflicts");
  await page.getByLabel(/Country codes/).fill("US");
  await page.getByRole("button", { name: /Senior Python SDE/ }).click();
  await expect(page.getByLabel(/Country codes/)).toHaveValue("IN");
  await expect(page.getByRole("combobox", { name: "Do you need sponsorship?", exact: true })).toHaveValue("yes");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(accessibility.violations.filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("preferences-results.png"), fullPage: true });
  expect(unexpected).toEqual([]);
  expect(requests.every((request) => !("answers" in request))).toBe(true); // Search never creates a submission.
});
