import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("homepage presents the product without horizontal overflow", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "HireWiz" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Build your workspace/i })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("public homepage has no automatically detectable accessibility violations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "HireWiz" })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("publishes the AdSense ownership metadata and authorized seller record", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.locator('meta[name="google-adsense-account"]')).toHaveAttribute(
    "content",
    "ca-pub-3196140381767962",
  );

  const adsTxtResponse = await request.get("/ads.txt");
  expect(adsTxtResponse.ok()).toBe(true);
  expect((await adsTxtResponse.text()).trim()).toBe(
    "google.com, pub-3196140381767962, DIRECT, f08c47fec0942fa0",
  );
});
