import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm, mkdir, cp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { chromium, expect } from "@playwright/test";
import { startFixtureServer } from "../fixtures/server.js";
import { fixtureBuild } from "../scripts/fixture-build.js";

async function browserFixture({ disabled = false } = {}) {
  const server = await startFixtureServer();
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-companion-`);
  let context;
  try {
  const extension = `${directory}/extension`;
  if (disabled) await cp(new URL("../extension", import.meta.url), extension, { recursive: true });
  else await fixtureBuild(extension, server.authority.configuration);
  const launch = async () => {
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: "chromium", headless: true, args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    const extensionId = new URL(worker.url()).host;
    const portal = await context.newPage(); await portal.goto(`${server.origin}/fixture/apply`);
    const popup = await context.newPage(); await popup.goto(`chrome-extension://${extensionId}/popup.html`);
    return { worker, extensionId, portal, popup };
  };
  const { worker, extensionId, portal, popup } = await launch();
  await context.addCookies([{ name: "portal-session", value: "synthetic-cookie-must-not-leave-portal", url: server.origin, httpOnly: true }]);
  if (!disabled) {
    await popup.getByRole("button", { name: "Allow exact local fixture origins" }).click();
    await popup.getByRole("button", { name: "Connect this device" }).click();
    await expect(popup.getByRole("button", { name: "Inspect and review package" })).toBeEnabled();
  }
  const prepare = async () => { await popup.getByRole("button", { name: "Inspect and review package" }).click(); await expect(popup.getByRole("heading", { name: "Exact fill review" })).toBeVisible(); };
  const approve = async () => { await popup.getByRole("checkbox").check(); await popup.getByRole("button", { name: "Approve and fill answers only" }).click(); };
  const noWrite = async () => { assert.deepEqual(await portal.evaluate(() => window.fixtureEvents), { input: 0, change: 0, submit: 0 }); await expect(portal.locator("#full_name")).toHaveValue(""); };
  return { ...server, context, portal, popup, worker, extensionId, prepare, approve, noWrite,
    async restart() { await context.close(); return { ...await launch(), context }; },
    async close() { await context.close(); await server.close(); await rm(directory, { recursive: true, force: true }); } };
  } catch (error) {
    await context?.close(); await server.close(); await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

test("real MV3 Chrome: exact review before autosave, device signing without cookie export, never submit", async () => {
  const f = await browserFixture(); try {
    await f.prepare(); await f.noWrite();
    await expect(f.popup.getByRole("button", { name: "Approve and fill answers only" })).toBeDisabled();
    await expect(f.popup.getByText("Alice Fixture", { exact: true })).toBeVisible();
    await expect(f.popup.getByText("alice@example.test", { exact: true })).toBeVisible();
    await expect(f.popup.getByText(/No upload or submission/)).toBeVisible();
    const directory = process.env.COMPANION_SCREENSHOT_DIR || `${process.cwd()}/test-results`;
    await mkdir(directory, { recursive: true });
    await f.popup.setViewportSize({ width: 390, height: 860 });
    assert.equal(await f.popup.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await f.popup.screenshot({ path: `${directory}/exact-review-390.png`, fullPage: true });
    await f.approve(); await expect(f.popup.locator("#result")).toContainText("has not submitted");
    await expect(f.portal.locator("#full_name")).toHaveValue("Alice Fixture");
    await expect(f.portal.locator("#privacy_consent")).toBeChecked();
    assert.deepEqual(await f.portal.evaluate(() => window.fixtureEvents), { input: 4, change: 4, submit: 0 });
    assert.equal(f.credentials.includes(true), false);
    const device = await f.popup.evaluate(async () => {
      const { deviceIdentity } = await import(chrome.runtime.getURL("device.js")); const identity = await deviceIdentity();
      let exported = true; try { await crypto.subtle.exportKey("jwk", identity.private_key); } catch { exported = false; }
      return { extractable: identity.private_key.extractable, exported };
    });
    assert.deepEqual(device, { extractable: false, exported: false });
    const checkpoint = await f.worker.evaluate(async () => (await chrome.storage.local.get("checkpoint")).checkpoint);
    assert.equal(checkpoint.status, "filled"); assert.equal(JSON.stringify(checkpoint).includes("Alice"), false); assert.equal(JSON.stringify(checkpoint).includes("@"), false);
    await f.popup.screenshot({ path: `${directory}/filled-only.png`, fullPage: true });
  } finally { await f.close(); }
});

test("real Chrome: shipping extension remains disabled with zero host grants and no authority requests", async () => {
  const f = await browserFixture({ disabled: true }); try {
    await expect(f.popup.locator("#mode")).toContainText("Disabled: production authorization");
    for (const label of ["Allow exact local fixture origins", "Connect this device", "Inspect and review package"]) await expect(f.popup.getByRole("button", { name: label })).toBeDisabled();
    await expect(f.popup.locator("#fill")).toBeDisabled(); await expect(f.popup.locator("#review")).toBeHidden();
    const permissions = await f.popup.evaluate(() => chrome.permissions.getAll());
    assert.deepEqual(permissions.origins, []);
    const response = await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "prepare", application_id: "app_fixture" }));
    assert.equal(response.ok, false); assert.match(response.error, /disabled/);
    assert.equal(f.authority.state.requests.length, 0); await f.noWrite();
  } finally { await f.close(); }
});

for (const human of ["login", "captcha", "mfa", "assessment"]) test(`real Chrome: ${human} requires human action without filling`, async () => {
  const f = await browserFixture(); try {
    await f.portal.locator("main").evaluate((node, value) => { node.dataset.checkpoint = value; }, human);
    await f.popup.getByRole("button", { name: "Inspect and review package" }).click();
    await expect(f.popup.getByRole("alert")).toContainText(`Human step required: ${human}`); await f.noWrite();
  } finally { await f.close(); }
});

for (const race of ["owner", "tenant", "form", "epoch", "approval", "permit"]) test(`real Chrome: ${race} changed after review blocks disclosure`, async () => {
  const f = await browserFixture(); try {
    await f.prepare();
    if (race === "owner") await f.portal.locator("main").evaluate((node) => { node.dataset.user = "other_owner"; });
    if (race === "tenant") await f.portal.locator("main").evaluate((node) => { node.dataset.tenant = "other_tenant"; });
    if (race === "form") await f.portal.locator("form").evaluate((form) => { const field = document.createElement("input"); field.id = "new_question"; field.required = true; form.append(field); });
    if (race === "epoch") f.authority.state.epoch = "fixture-epoch-new";
    if (race === "approval") f.authority.state.approval_revision += 1;
    if (race === "permit") f.authority.state.permit = false;
    await f.approve(); await expect(f.popup.getByRole("alert")).toBeVisible(); await f.noWrite();
  } finally { await f.close(); }
});

test("real Chrome: full browser restart retains unknown and cancel cannot erase ambiguity", async () => {
  const f = await browserFixture(); try {
    await f.prepare(); f.authority.state.fail_begin_response = true; await f.approve();
    await expect(f.popup.getByRole("alert")).toContainText("Begin response lost"); await f.noWrite();
    const deviceBefore = await f.popup.evaluate(async () => (await (await import(chrome.runtime.getURL("device.js"))).deviceIdentity()).device_id);
    Object.assign(f, await f.restart());
    await expect(f.popup.getByRole("alert")).toContainText("Previous fill may be incomplete");
    const deviceAfter = await f.popup.evaluate(async () => (await (await import(chrome.runtime.getURL("device.js"))).deviceIdentity()).device_id);
    assert.equal(deviceAfter, deviceBefore);
    await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "cancel" }));
    const response = await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "prepare", application_id: "app_fixture" }));
    assert.equal(response.ok, false); assert.match(response.error, /Previous fill/);
    assert.deepEqual(await f.portal.evaluate(() => window.fixtureEvents), { input: 0, change: 0, submit: 0 });
  } finally { await f.close(); }
});

test("real Chrome: cancel while the single-use begin response is pending prevents DOM writes", async () => {
  const f = await browserFixture(); try {
    await f.prepare(); f.authority.state.begin_delay_ms = 500;
    await f.approve(); await expect.poll(() => f.authority.begun.size).toBe(1);
    await f.popup.getByRole("button", { name: "Cancel further filling" }).click();
    await expect(f.popup.getByRole("alert")).toContainText("Cancelled"); await f.noWrite();
  } finally { await f.close(); }
});
