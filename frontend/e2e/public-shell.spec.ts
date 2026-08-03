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
