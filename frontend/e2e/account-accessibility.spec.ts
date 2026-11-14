import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

const csrfToken = "a".repeat(64);
const linkToken = "A".repeat(43);
const syntheticEmail = "fixture@example.invalid";

async function focusByTab(page: Page, control: Locator) {
  for (let step = 0; step < 40; step += 1) {
    if (await control.evaluate((element) => document.activeElement === element)) return;
    await page.keyboard.press("Tab");
  }
  await expect(control).toBeFocused();
}

async function checkAccessibility(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  const { violations } = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
}

test.beforeEach(async ({ page, baseURL }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const origin = new URL(baseURL!).origin;
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin || request.method() !== "GET") return route.abort();
    if (url.pathname === "/api/auth/session") return route.fulfill({ json: {} });
    if (url.pathname === "/api/auth/providers") return route.fulfill({ json: { google: { id: "google" } } });
    if (url.pathname === "/api/auth/csrf") return route.fulfill({ json: { csrfToken } });
    if (url.pathname.startsWith("/api/")) return route.abort();
    return route.continue();
  });
});

test("login fields and Google consent work with the keyboard", async ({ page }) => {
  await page.goto("/login");
  const email = page.getByRole("textbox", { name: "Email address" });
  await expect(email).toBeEnabled();
  const consent = page.getByRole("checkbox", { name: /If Google creates a new account/i });
  const google = page.getByRole("button", { name: "Continue with Google" });
  await expect(google).toBeDisabled();
  await focusByTab(page, email);
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
  await focusByTab(page, consent);
  await page.keyboard.press("Space");
  await expect(google).toBeEnabled();
  await focusByTab(page, google);
  await checkAccessibility(page);
});

for (const [url, button, status] of [
  ["/forgot-password", "Send reset link", "RESET_LINK_REQUESTED"],
  ["/privacy-erasure", "Send confirmation link", "ERASURE_LINK_REQUESTED"],
] as const) {
  test(`${url} keyboard submission announces the synthetic result`, async ({ page }) => {
    const requests: unknown[] = [];
    const api = url === "/forgot-password" ? "/api/password-reset/request" : "/api/privacy-erasure/request";
    await page.route(`**${api}`, async (route) => {
      expect(route.request().method()).toBe("POST");
      requests.push(route.request().postDataJSON());
      await route.fulfill({ status: 202, json: { status } });
    });
    await page.goto(url);
    const email = page.getByRole("textbox", { name: "Email address" });
    await expect(email).toBeEnabled();
    await focusByTab(page, email);
    await page.keyboard.insertText(syntheticEmail);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: button })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Check your email" })).toBeVisible();
    expect(requests).toEqual([{ email: syntheticEmail }]);
    await checkAccessibility(page);
  });
}

test("reset password announces a mismatched pair without sending it", async ({ page }) => {
  let requests = 0;
  await page.route("**/api/password-reset/confirm", async (route) => {
    requests += 1;
    await route.abort();
  });
  await page.goto(`/reset-password#token=${linkToken}`);
  const password = page.getByLabel("New password", { exact: true });
  const confirmation = page.getByLabel("Confirm new password", { exact: true });
  await expect(password).toHaveAccessibleDescription(/10–128 characters/);
  await expect(page).toHaveURL(/\/reset-password$/);
  await focusByTab(page, password);
  await page.keyboard.insertText("fixture-password-1");
  await page.keyboard.press("Tab");
  await expect(confirmation).toBeFocused();
  await page.keyboard.insertText("fixture-password-2");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Update password" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("The passwords do not match");
  expect(requests).toBe(0);
  await checkAccessibility(page);
});

test("privacy erasure requires explicit keyboard consent and announces refusal", async ({ page }) => {
  const requests: unknown[] = [];
  await page.route("**/api/privacy-erasure/confirm", async (route) => {
    expect(route.request().method()).toBe("POST");
    requests.push(route.request().postDataJSON());
    await route.fulfill({ status: 429, json: { status: "RATE_LIMITED" } });
  });
  await page.goto(`/privacy-erasure/confirm#token=${linkToken}`);
  const consent = page.getByRole("checkbox", { name: /permanently delete this account and its saved data/ });
  const submit = page.getByRole("button", { name: "Permanently delete account" });
  await expect(submit).toBeDisabled();
  await expect(page).toHaveURL(/\/privacy-erasure\/confirm$/);
  await focusByTab(page, consent);
  await page.keyboard.press("Space");
  await expect(submit).toBeEnabled();
  await checkAccessibility(page);
  await page.keyboard.press("Tab");
  await expect(submit).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Too many attempts. Please wait before trying again.");
  await expect(submit).toBeDisabled();
  expect(requests).toEqual([{ token: linkToken, confirm_erasure: true }]);
  await checkAccessibility(page);
});
