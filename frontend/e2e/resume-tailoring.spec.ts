import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { encode } from "next-auth/jwt";

const opportunityId = "opp_tailoring_fixture";
const workspacePath = `/workspace/${opportunityId}?tab=resume`;
const now = "2026-10-07T10:00:00Z";
const sourceFile = { name: "Taylor-resume.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nLocal upload fixture\n%%EOF") };
type ResumeFixture = { id: number; filename: string; created_at: string; source_available?: boolean; source_format?: "pdf" | "docx" | null };
type RequestRecord = { path: string; method: string; query: string; body: unknown };
const browserErrors = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  browserErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !/Failed to load resource: the server responded with a status of (404|503)/.test(message.text())) errors.push(message.text());
  });
});

test.afterEach(async ({ page }) => {
  expect(browserErrors.get(page)).toEqual([]);
});

function createFixture() {
  const resumes: ResumeFixture[] = [
    { id: 1, filename: sourceFile.name, created_at: now, source_available: false, source_format: null },
    { id: 2, filename: sourceFile.name, created_at: now, source_available: true, source_format: "pdf" },
  ];
  const evidence = [
    { id: "evi_old", title: "Earlier service performance work", category: "work", confidence: "high", approval_state: "approved", resume_id: 1, evidence_text: "Improved API response time by 20% through query tuning.", skills: ["PostgreSQL"], metrics: {}, provenance: "resume", source_ref: "experience", created_at: now, updated_at: now },
  ];
  const opportunity = {
    id: opportunityId, title: "Software Engineer, Search", company: "Example Studio", location: "Remote",
    priority: "high", stage: "evaluating", source: "manual", source_url: null, compensation: null,
    deadline_at: null, archived_at: null, created_at: now, updated_at: now, resume_id: 1 as number | null,
    latest_match_id: null, latest_analysis_run_id: null, next_action: null, notes: "", outcome: null,
    outcome_notes: null, outcome_at: null, job_description: "Build reliable services with PostgreSQL and Python.",
    job_snapshot: {}, activity: [], contacts: [], reminders: [], resume_versions: [] as Record<string, unknown>[],
  };
  const requests: RequestRecord[] = [];
  const unexpected: RequestRecord[] = [];
  const state = { listError: false, connectionError: false, parseCount: 0, connected: false, heldOpportunityReads: 0, pauseList: null as Promise<void> | null, pauseConnectedOpportunity: null as Promise<void> | null };
  const patchResponses: Record<string, unknown>[] = [];

  async function install(context: BrowserContext, baseURL: string) {
    // A signed dummy cookie passes local middleware; every backend request stays mocked.
    const token = await encode({
      secret: process.env.PLAYWRIGHT_AUTH_SECRET || "playwright-secret-at-least-thirty-two-characters",
      token: { sub: "424242", email: "fixture@example.com", name: "Taylor Example", hirewizUserId: 424242, accessToken: "local-fixture-token" },
      maxAge: 3600,
    });
    await context.addCookies([{ name: "next-auth.session-token", value: token, url: baseURL, httpOnly: true, sameSite: "Lax" }]);
    await context.addInitScript(() => {
      if (location.protocol !== "http:" && location.protocol !== "https:") return;
      localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "essential", savedAt: new Date().toISOString() }));
    });
    await context.route("**/api/backend/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.replace("/api/backend", "");
      const method = request.method();
      const body = method === "GET" ? undefined : request.headers()["content-type"]?.includes("application/json") ? request.postDataJSON() : request.postData();
      const record = { path, method, query: url.search, body };
      requests.push(record);
      let status = 200;
      let value: unknown;
      if (path === "/auth/profile") value = { email: "fixture@example.com", full_name: "Taylor Example", tier: "free", ai_credits: 40 };
      else if (path === "/v1/features") value = { features: { career_workspace: { enabled: true }, evidence_tailoring: { enabled: true }, async_analysis: { enabled: true } } };
      else if (path === "/resume/list") {
        if (state.pauseList) await state.pauseList;
        status = state.listError ? 503 : 200;
        value = state.listError ? { detail: "Source metadata temporarily unavailable." } : { resumes };
      } else if (path === `/v1/opportunities/${opportunityId}`) {
        if (method === "PATCH" && state.connectionError) { status = 503; value = { detail: "Could not connect this resume." }; }
        else if (method === "PATCH") {
          Object.assign(opportunity, body);
          state.connected = true;
          // PATCH returns the base opportunity, while GET includes the detail arrays.
          const base: Record<string, unknown> = { ...opportunity };
          for (const key of ["activity", "contacts", "reminders", "resume_versions"]) delete base[key];
          patchResponses.push(base);
          value = base;
        } else {
          if (state.connected && state.pauseConnectedOpportunity) { state.heldOpportunityReads++; await state.pauseConnectedOpportunity; }
          value = opportunity;
        }
      } else if (path === "/v1/opportunities") value = { items: [opportunity], total: 1 };
      else if (path === "/v1/evidence-items") value = evidence.filter((entry) => entry.resume_id === Number(url.searchParams.get("resume_id")));
      else if (path.startsWith("/v1/evidence-items/import-resume/") && method === "POST") {
        const resumeId = Number(path.split("/").at(-1));
        const imported = { ...evidence[0], id: `evi_upload_${resumeId}`, title: "New upload service performance work", approval_state: "pending", resume_id: resumeId };
        evidence.push(imported);
        value = { created: [imported], skipped: 0 };
      } else if (path.startsWith("/v1/evidence-items/") && method === "PATCH") {
        const entry = evidence.find((item) => item.id === path.split("/").at(-1));
        if (!entry) { status = 404; value = { detail: "Evidence not found." }; }
        else { Object.assign(entry, body); value = entry; }
      } else if (path === "/v1/resume-versions" && method === "POST") {
        value = { id: "rsv_snapshot", version_number: 1, approval_state: "draft", generation_run_id: null, rendered_artifact_ref: null, submitted_at: null, created_at: now, ...body };
        opportunity.resume_versions.push(value as Record<string, unknown>);
        status = 201;
      } else if (path === "/resume/parse" && method === "POST") {
        state.parseCount++;
        if (!resumes.some((resume) => resume.id === 3)) resumes.push({ id: 3, filename: sourceFile.name, created_at: now, source_available: true, source_format: "pdf" });
        value = { resume_id: 3, source_available: true, source_format: "pdf", skills: ["PostgreSQL", "Python"], experience_years: 4, sections: { experience: evidence[0].evidence_text }, education: [], certifications: [], projects: [], text_preview: evidence[0].evidence_text };
      } else {
        unexpected.push(record);
        status = 404;
        value = { detail: "Unexpected request: no local fixture defined." };
      }
      await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    });
  }
  return { resumes, evidence, opportunity, requests, unexpected, patchResponses, state, install };
}

async function openWorkspace(page: Page) {
  await page.goto(workspacePath);
  await expect(page.getByRole("button", { name: "Resume & evidence", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: "Review changes before approval", exact: true })).toBeVisible();
}

function actions(page: Page) {
  return {
    generate: page.getByRole("button", { name: "Generate tailored version", exact: true }),
    snapshot: page.getByRole("button", { name: "Save source snapshot", exact: true }),
    status: page.locator("#resume-actions-status"),
    resume: page.getByRole("combobox", { name: "Resume", exact: true }),
  };
}

async function audit(page: Page) {
  await expect(page).toHaveTitle(/HireWiz/);
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
}

test("approved evidence with a missing original explains both disabled actions and the recovery path", async ({ page, context, baseURL }, testInfo) => {
  const fixture = createFixture();
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  const { generate, snapshot, status, resume } = actions(page);
  await expect(status).toContainText("Your evidence is approved, but the selected resume's original file is missing.");
  await expect(generate).toBeDisabled();
  await expect(snapshot).toBeDisabled();
  await expect(generate).toHaveAttribute("aria-describedby", "resume-actions-status");
  await expect(snapshot).toHaveAttribute("aria-describedby", "resume-actions-status");
  await expect(page.getByRole("link", { name: "Upload source again", exact: true })).toHaveAttribute("href", `/resume?opportunity=${opportunityId}`);
  await expect(resume.locator("option[value='1']")).toHaveText(`${sourceFile.name} · #1 · needs upload`);
  await expect(resume.locator("option[value='2']")).toHaveText(`${sourceFile.name} · #2 · PDF original saved`);
  await page.getByRole("button", { name: "Choose resume", exact: true }).click();
  await expect(resume).toBeFocused();
  await expect(resume).toHaveValue("1");
  expect(fixture.requests.filter((request) => request.method === "POST")).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
  if (testInfo.project.name === "mobile-390" || testInfo.project.name === "desktop-1440") {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: testInfo.outputPath("missing-original-recovery.png"), fullPage: true, scale: "css" });
  }
});

test("unknown source metadata can be refreshed without claiming the original is missing", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  delete fixture.resumes[0].source_available;
  delete fixture.resumes[0].source_format;
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  const { generate, snapshot, status } = actions(page);
  await expect(status).toContainText("original file status is unknown");
  await expect(generate).toBeDisabled();
  await expect(snapshot).toBeDisabled();
  await expect(page.getByRole("link", { name: "Upload source again", exact: true })).toHaveCount(0);
  const before = fixture.requests.length;
  Object.assign(fixture.resumes[0], { source_available: true, source_format: "pdf" });
  await page.getByRole("button", { name: "Refresh details", exact: true }).click();
  await expect(generate).toBeEnabled();
  await expect(snapshot).toBeEnabled();
  const refreshed = fixture.requests.slice(before);
  expect(refreshed.some((request) => request.path === "/resume/list")).toBe(true);
  expect(refreshed.some((request) => request.path === `/v1/opportunities/${opportunityId}`)).toBe(true);
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
});

test("a source details request failure has an enabled retry that restores the actions", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  Object.assign(fixture.resumes[0], { source_available: true, source_format: "pdf" });
  fixture.state.listError = true;
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  const { generate, snapshot, status } = actions(page);
  await expect(status).toContainText("Could not load the selected resume's source details");
  await expect(generate).toBeDisabled();
  await expect(snapshot).toBeDisabled();
  const refresh = page.getByRole("button", { name: "Refresh details", exact: true });
  await expect(refresh).toBeEnabled();
  await expect(page.getByRole("link", { name: "Upload source again", exact: true })).toHaveCount(0);
  fixture.state.listError = false;
  await refresh.click();
  await expect(generate).toBeEnabled();
  await expect(snapshot).toBeEnabled();
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
});

test("a retained original allows a source snapshot without approved evidence", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  fixture.opportunity.resume_id = 2;
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  const { generate, snapshot, status } = actions(page);
  await expect(status).toContainText("A source snapshot does not require approved evidence.");
  await expect(generate).toBeDisabled();
  await expect(snapshot).toBeEnabled();
  await snapshot.click();
  await expect(page.getByRole("heading", { name: "Example Studio source snapshot", exact: true })).toBeVisible();
  expect(fixture.requests.find((request) => request.path === "/v1/resume-versions" && request.method === "POST")?.body).toEqual({
    resume_id: 2, opportunity_id: opportunityId, label: "Example Studio source snapshot",
    structured_content: { format_preservation: "source", source_format: "pdf", source_edits: [] }, evidence_ids: [],
  });
  expect(fixture.requests.some((request) => request.path === "/v1/analysis-runs")).toBe(false);
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
});

test("choosing a retained upload loads its own facts and does not transfer the earlier approval", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  const { generate, snapshot, resume } = actions(page);
  await expect(resume).toHaveValue("1");
  await page.getByRole("button", { name: "Choose resume", exact: true }).click();
  await resume.selectOption("2");
  await expect(resume).toHaveValue("2");
  await expect(snapshot).toBeEnabled();
  await expect(generate).toBeDisabled();
  await expect(page.getByRole("heading", { name: "No evidence imported", exact: true })).toBeVisible();
  expect(fixture.requests.find((request) => request.path === `/v1/opportunities/${opportunityId}` && request.method === "PATCH")?.body).toEqual({ resume_id: 2 });
  expect(fixture.requests.some((request) => request.path === "/v1/evidence-items" && request.query === "?resume_id=2")).toBe(true);
  await page.getByRole("button", { name: "Import from resume", exact: true }).click();
  await expect(page.getByRole("heading", { name: "New upload service performance work", exact: true })).toBeVisible();
  await expect(generate).toBeDisabled();
  await page.getByRole("button", { name: "Approve evidence", exact: true }).click();
  await expect(generate).toBeEnabled();
  expect(fixture.requests.find((request) => request.path === "/v1/evidence-items/evi_upload_2" && request.method === "PATCH")?.body).toEqual({ approval_state: "approved" });
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
});

test("upload recovery uses the new resume immediately while the return GET is delayed", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  await page.getByRole("link", { name: "Upload source again", exact: true }).click();
  await expect(page.getByRole("link", { name: "Back to your opportunity", exact: true })).toHaveAttribute("href", workspacePath);
  await page.locator("input[type=file]").setInputFiles(sourceFile);
  await page.getByRole("button", { name: "Parse resume", exact: true }).click();
  await expect(page.getByText("Resume parsed", { exact: true })).toBeVisible();
  expect(fixture.opportunity.resume_id).toBe(1);
  expect(fixture.requests.filter((request) => request.method === "PATCH")).toEqual([]);
  let release = () => {};
  fixture.state.pauseConnectedOpportunity = new Promise<void>((resolve) => { release = resolve; });
  try {
    await page.getByRole("button", { name: "Use for this opportunity", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/workspace/${opportunityId}\\?tab=resume$`));
    await expect(page.getByRole("button", { name: "Resume & evidence", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => fixture.state.heldOpportunityReads).toBeGreaterThan(0);
    const { generate, snapshot, resume } = actions(page);
    await expect(resume).toHaveValue("3");
    await expect(snapshot).toBeEnabled();
    await expect(generate).toBeDisabled();
    await expect(page.getByRole("heading", { name: "No evidence imported", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Import from resume", exact: true }).click();
    await expect(page.getByRole("heading", { name: "New upload service performance work", exact: true })).toBeVisible();
    await expect(generate).toBeDisabled();
    await page.getByRole("button", { name: "Approve evidence", exact: true }).click();
    await expect(generate).toBeEnabled();
  } finally { release(); }
  expect(fixture.state.parseCount).toBe(1);
  expect(fixture.requests.find((request) => request.path === `/v1/opportunities/${opportunityId}` && request.method === "PATCH")?.body).toEqual({ resume_id: 3 });
  expect(fixture.patchResponses[0]).not.toHaveProperty("resume_versions");
  expect(fixture.requests.some((request) => request.path === "/v1/evidence-items" && request.query === "?resume_id=3")).toBe(true);
  expect(fixture.requests.some((request) => request.path === "/v1/analysis-runs")).toBe(false);
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
});

test("a connection failure preserves the upload and retries without parsing again", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  fixture.state.connectionError = true;
  await fixture.install(context, baseURL!);
  await page.goto(`/resume?opportunity=${opportunityId}`);
  await page.locator("input[type=file]").setInputFiles(sourceFile);
  await page.getByRole("button", { name: "Parse resume", exact: true }).click();
  const connect = page.getByRole("button", { name: "Use for this opportunity", exact: true });
  await connect.click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Your upload is saved. Retry connecting it.");
  await expect(page.getByText("Resume parsed", { exact: true })).toBeVisible();
  await expect(connect).toBeEnabled();
  expect(fixture.opportunity.resume_id).toBe(1);
  await audit(page);
  fixture.state.connectionError = false;
  await connect.click();
  await expect(actions(page).resume).toHaveValue("3");
  expect(fixture.state.parseCount).toBe(1);
  expect(fixture.requests.filter((request) => request.path === `/v1/opportunities/${opportunityId}` && request.method === "PATCH")).toHaveLength(2);
  expect(fixture.unexpected).toEqual([]);
});

test("an ordinary upload keeps the existing add-target-role flow", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  await fixture.install(context, baseURL!);
  await page.goto("/resume");
  await page.locator("input[type=file]").setInputFiles(sourceFile);
  await page.getByRole("button", { name: "Parse resume", exact: true }).click();
  await expect(page.getByRole("link", { name: "Add target role", exact: true })).toHaveAttribute("href", "/workspace?new=1");
  await expect(page.getByRole("button", { name: "Use for this opportunity", exact: true })).toHaveCount(0);
  expect(fixture.requests.filter((request) => request.method === "PATCH")).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
  await audit(page);
});

test("a missing resume row offers refresh and selection without calling it a legacy source", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  fixture.resumes.splice(0, 1);
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  await expect(actions(page).status).toContainText("connected resume is missing from the loaded resume list");
  await expect(actions(page).resume).toHaveValue("1");
  await expect(page.getByRole("button", { name: "Refresh details", exact: true })).toBeEnabled();
  await expect(page.getByRole("link", { name: "Upload source again", exact: true })).toHaveCount(0);
  await expect(actions(page).generate).toBeDisabled();
  await expect(actions(page).snapshot).toBeDisabled();
  expect(fixture.unexpected).toEqual([]);
});

test("no selected resume keeps selection explicit even when a retained original is available", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  fixture.opportunity.resume_id = null;
  await fixture.install(context, baseURL!);
  await openWorkspace(page);
  await expect(actions(page).status).toContainText("Choose a resume above");
  await expect(actions(page).resume).toHaveValue("");
  await expect(actions(page).generate).toBeDisabled();
  await expect(actions(page).snapshot).toBeDisabled();
  await page.getByRole("button", { name: "Choose resume", exact: true }).click();
  await expect(actions(page).resume).toBeFocused();
  expect(fixture.requests.filter((request) => request.method === "PATCH")).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
});

test("loading source details blocks both actions until metadata arrives", async ({ page, context, baseURL }) => {
  const fixture = createFixture();
  Object.assign(fixture.resumes[0], { source_available: true, source_format: "pdf" });
  let release = () => {};
  fixture.state.pauseList = new Promise<void>((resolve) => { release = resolve; });
  await fixture.install(context, baseURL!);
  try {
    await openWorkspace(page);
    await expect(actions(page).status).toContainText("Checking whether the selected resume has its original file");
    await expect(actions(page).generate).toBeDisabled();
    await expect(actions(page).snapshot).toBeDisabled();
    await expect(page.getByRole("link", { name: "Upload source again", exact: true })).toHaveCount(0);
  } finally { release(); }
  await expect(actions(page).generate).toBeEnabled();
  await expect(actions(page).snapshot).toBeEnabled();
  expect(fixture.unexpected).toEqual([]);
});
