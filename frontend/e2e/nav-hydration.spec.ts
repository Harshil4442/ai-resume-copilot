import { expect, test } from "@playwright/test";

test("navigation waits for hydration before accepting a mobile menu action", async ({ page, context, baseURL }, testInfo) => {
  let releaseChunks!: () => void;
  let firstChunkHeld!: () => void;
  const chunksReleased = new Promise<void>((resolve) => { releaseChunks = resolve; });
  const chunkHeld = new Promise<void>((resolve) => { firstChunkHeld = resolve; });
  const origin = new URL(baseURL!).origin;
  let heldChunks = 0;
  let externalRequests = 0;
  let pageErrors = 0;

  await context.addInitScript(() => {
    localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "essential", savedAt: new Date().toISOString() }));
  });
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) {
      externalRequests += 1;
      await route.abort();
      return;
    }
    if (url.pathname.startsWith("/_next/static/") && url.pathname.endsWith(".js")) {
      heldChunks += 1;
      firstChunkHeld();
      await chunksReleased;
    }
    await route.continue();
  });
  page.on("pageerror", () => { pageErrors += 1; });

  try {
    // DOMContentLoaded waits for deferred scripts. Commit allows inspection of
    // the streamed server HTML while the actual Next JavaScript stays gated.
    await page.goto("/", { waitUntil: "commit" });
    const menu = page.getByRole("button", { name: "Open navigation", exact: true, includeHidden: true });
    await expect(page.locator("main h1")).toBeVisible();
    await expect(menu).toHaveCount(1);
    await chunkHeld;
    const beforeHydration = await menu.evaluate((element) => ({
      disabled: (element as HTMLButtonElement).disabled,
      expanded: element.getAttribute("aria-expanded"),
    }));
    await testInfo.attach("server-menu-readiness", {
      body: JSON.stringify({ ...beforeHydration, heldChunks }),
      contentType: "application/json",
    });
    expect(heldChunks).toBeGreaterThan(0);
    await expect(menu).toBeDisabled();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toHaveCount(0);

    releaseChunks();
    await expect(menu).toBeEnabled();
    if (page.viewportSize()!.width < 1280) {
      await expect(menu).toBeVisible();
      await menu.click();
      const close = page.getByRole("button", { name: "Close navigation", exact: true });
      await expect(close).toHaveAttribute("aria-expanded", "true");
      await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible();
      await close.click();
      await expect(menu).toHaveAttribute("aria-expanded", "false");
      await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toHaveCount(0);
    } else {
      await expect(menu).toBeHidden();
      await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    }
    expect(externalRequests).toBe(0);
    expect(pageErrors).toBe(0);
  } finally {
    releaseChunks();
    await context.unrouteAll({ behavior: "wait" });
  }
});
