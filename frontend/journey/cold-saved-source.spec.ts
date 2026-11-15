import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page, baseURL: string, email: string) {
  await page.goto(baseURL + "/login");
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("synthetic-browser-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test("saved originals remain exact and private while new upload admission is paused", async ({ page, context, browser, baseURL }) => {
  if (!baseURL || new URL(baseURL).origin !== baseURL || !/^https:\/\/127\.0\.0\.1:\d+$/.test(baseURL)) {
    throw new Error("A disposable TLS loopback origin is required");
  }
  const resume = process.env.COLD_BROWSER_ARCHIVE_RESUME ?? "";
  expect(resume).toMatch(/^[1-9]\d*$/);
  const evidence = process.env.COLD_BROWSER_EVIDENCE!;
  const original = await readFile(join(evidence, "retained-original.pdf"));
  expect(original.length).toBe(4_980_736);
  const expectedHash = createHash("sha256").update(original).digest("hex");
  const corrupt = Buffer.from(original);
  corrupt[1] ^= 1;
  const routePath = `/api/backend/resume/${resume}`;
  let storageReads = 0;
  let legacyReads = 0;
  const unexpected: string[] = [];
  const responses: string[] = [];
  const downloads: string[] = [];
  await context.route((url) => url.origin !== baseURL, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.protocol === "blob:") return route.continue();
    const expectedPath = "/synthetic-resumes/clean/rup_" + "2".repeat(32) + "/source";
    if (url.origin !== "https://storage.googleapis.com" || url.pathname !== expectedPath) {
      unexpected.push("external_route");
      return route.abort("blockedbyclient");
    }
    expect(request.method()).toBe("GET");
    expect(url.searchParams.get("generation")).toBe("1");
    const headers = await request.allHeaders();
    for (const name of ["authorization", "cookie", "referer"]) expect(headers[name]).toBeUndefined();
    storageReads++;
    return route.fulfill({ status: 200, body: storageReads === 3 ? corrupt : original,
      headers: { "Content-Type": "application/pdf", "Content-Length": String(original.length),
        "Access-Control-Allow-Origin": baseURL, "Cache-Control": "private, no-store" } });
  });
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === routePath + "/source") legacyReads++;
  });
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === routePath + "/source-access") {
      responses.push(response.headers()["cache-control"] ?? "");
    }
  });
  page.on("download", (download) => downloads.push(download.suggestedFilename()));

  await signIn(page, baseURL, "archive-owner.browser@example.com");
  const disabled = await context.request.post(baseURL + "/api/backend/resume/uploads", {
    headers: { Origin: baseURL, "Idempotency-Key": "owned-paused-source-archive" },
    data: { filename: "new.pdf", size_bytes: original.length, sha256: expectedHash },
  });
  expect(disabled.status()).toBe(503);
  expect((await disabled.json()).detail.code).toBe("direct_upload_unavailable");

  await page.goto(baseURL + `/resume/preview?resume=${resume}`);
  await expect(page.getByTitle("Original resume: retained-original.pdf")).toBeVisible();
  const button = page.getByRole("button", { name: "Download original PDF", exact: true });
  const downloaded = page.waitForEvent("download", (event) => event.suggestedFilename() === "retained-original.pdf");
  await button.click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe("retained-original.pdf");
  const bytes = await readFile((await file.path())!);
  expect(bytes.length).toBe(original.length);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(expectedHash);

  // Headless Chromium can emit its own download for the embedded PDF viewer.
  // The refused click must start no download beyond the already verified one.
  const downloadsBeforeCorrupt = downloads.length;
  await button.click();
  await expect(page.getByText("Could not verify the original resume file. Please try again.", { exact: true })).toHaveAttribute("role", "alert");
  expect(downloads).toHaveLength(downloadsBeforeCorrupt);
  expect(legacyReads).toBe(0);
  expect(responses).toHaveLength(3);
  for (const header of responses) {
    expect(header.split(",").map((directive) => directive.trim())).toEqual(expect.arrayContaining(["private", "no-store"]));
  }

  const foreign = await browser.newContext({ ignoreHTTPSErrors: true });
  await foreign.route((url) => url.origin !== baseURL, (route) => route.abort("blockedbyclient"));
  try {
    await signIn(await foreign.newPage(), baseURL, "archive-foreign.browser@example.com");
    const denied = await foreign.request.get(baseURL + routePath + "/source-access");
    expect(denied.status()).toBe(404);
    expect((await denied.json()).detail.code).toBe("resume_not_found");
    expect(await denied.text()).not.toContain("storage.googleapis.com");
  } finally {
    await foreign.close();
  }
  expect(storageReads).toBe(3);
  expect(unexpected).toEqual([]);
  await writeFile(process.env.COLD_BROWSER_BROWSER_PROOF!, JSON.stringify({
    synthetic_only: true, native_gcs_proof: false, signed_url_transport_is_intercepted: true,
    bytes: original.length, sha256: expectedHash, upload_paused: true, corrupt_download_refused: true,
    cross_owner_denied: true, legacy_fallback_reads: legacyReads, synthetic_storage_reads: storageReads,
  }, null, 2) + "\n");
});
