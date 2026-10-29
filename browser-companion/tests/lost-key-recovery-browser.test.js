// Owned offline Chrome profiles only. Never load the owner's installed extension.
import assert from "node:assert/strict";
import test from "node:test";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { chromium, expect } from "@playwright/test";
import { nativeFixtureIdentity } from "../scripts/native-fixture-build.js";
import { CONFIG } from "../extension/config.js";
const sourceVersion = JSON.parse(await readFile(new URL("../extension/manifest.json", import.meta.url), "utf8")).version;

const id = "12345678-1234-1234-1234-123456789abc";
const old = { pairing_checkpoint: { version: 2, device_id: id, phase: "unknown", send_kind: "complete", marker: "retained-old-connection" },
  assistance_checkpoint: { version: 1, device_id: id, phase: "unknown", application_id: "retained-old-application", review_id: id, filled_count: 0 },
  native_application_evidence: { [`submit:${id}`]: { outcome: "unknown", marker: "retained-exact-unknown-proof" } } };

async function fixture() {
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-owned-offline-lost-key-`);
  const extension = `${directory}/extension`, identity = nativeFixtureIdentity();
  await cp(new URL("../extension/", import.meta.url), extension, { recursive: true });
  const manifest = JSON.parse(await readFile(`${extension}/manifest.json`, "utf8"));
  Object.assign(manifest, { name: "HireWiz — OWNED OFFLINE RECOVERY TEST", key: identity.manifestKey, host_permissions: [] });
  await writeFile(`${extension}/manifest.json`, JSON.stringify(manifest));
  await writeFile(`${extension}/config.js`, `export const CONFIG = Object.freeze(${JSON.stringify({ ...CONFIG, extensionId: identity.extensionId })});\n`);
  await writeFile(`${extension}/owned-recovery-probe.html`, "<!doctype html><title>Owned offline recovery probe</title>");
  let context;
  try {
    context = await chromium.launchPersistentContext(`${directory}/synthetic-profile`, { channel: "chromium", headless: true,
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] });
    await context.setOffline(true);
    const requests = []; context.on("request", (request) => { if (request.url().startsWith("https:")) requests.push(request.url()); });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    assert.equal(new URL(worker.url()).host, identity.extensionId);
    await worker.evaluate((saved) => chrome.storage.local.set(saved), old);
    const probe = await context.newPage(); await probe.goto(`chrome-extension://${identity.extensionId}/owned-recovery-probe.html`);
    const popup = await context.newPage();
    const open = async () => {
      await popup.goto(`chrome-extension://${identity.extensionId}/popup.html`);
      await expect(popup.locator("#error")).toContainText("HW-COMPANION-START-KEY-MISSING-RETAINED");
      await expect(popup.locator("#error")).toContainText(`Installed version: ${sourceVersion}`);
    };
    return { context, worker, popup, probe, requests, open,
      async close() { await context.close(); await rm(directory, { recursive: true, force: true }); } };
  } catch (failure) { await context?.close(); await rm(directory, { recursive: true, force: true }); throw failure; }
}

test("actual offline MV3 review and trusted confirmed commit preserve old unknown outcomes and create only a fresh isolated identity", async () => {
  const f = await fixture();
  try {
    await f.open();
    await expect(f.popup.locator("#recovery-review")).toBeEnabled();
    await expect(f.popup.locator("#assistance")).toBeHidden();
    // Programmatic/untrusted clicks cannot initiate even a recovery review.
    await f.popup.evaluate(() => document.getElementById("recovery-review").click());
    await expect(f.popup.locator("#recovery-confirmation")).toBeHidden();
    await f.popup.locator("#recovery-review").click();
    await expect(f.popup.locator("#recovery-confirmation")).toBeVisible();
    await expect(f.popup.locator("#recovery-confirm")).toBeDisabled();
    assert.equal(await f.probe.evaluate(async () => (await new (await import(chrome.runtime.getURL("lost-key-recovery.js"))).RecoveryJournal().read()).record), null);
    await f.popup.evaluate(() => { chrome.tabs.create = async ({ url }) => { window.syntheticReviewedUrl = url; return {}; }; });
    await f.popup.getByRole("button", { name: `Review old device ${id} on HireWiz` }).click();
    assert.equal(await f.popup.evaluate(() => window.syntheticReviewedUrl), `${CONFIG.websiteOrigin}/browser-companion?native_device=${id}`);
    await f.popup.locator("#recovery-ack").check();
    await f.popup.locator("#recovery-confirm").click();
    await expect(f.popup.locator("#recovery-info")).toContainText("new local identity is stored separately");
    const result = await f.probe.evaluate(async () => {
      const { RecoveryJournal } = await import(chrome.runtime.getURL("lost-key-recovery.js"));
      const { deviceIdentity } = await import(chrome.runtime.getURL("device.js"));
      const { record, legacyMissing } = await new RecoveryJournal().read();
      const identity = await deviceIdentity({ requireRetained: true, slot: `device-recovery:${record.id}` });
      return { record, legacyMissing, extractable: identity.private_key.extractable, new_id: identity.device_id,
        saved: await chrome.storage.local.get(null), no_old_authority: record.snapshot.assistance_checkpoint.phase === "unknown" };
    });
    assert.equal(result.record.phase, "active"); assert.equal(result.legacyMissing, true); assert.equal(result.extractable, false);
    assert.notEqual(result.new_id, id); assert.deepEqual(result.record.snapshot, old); assert.deepEqual(result.saved, old);
    await expect(f.popup.locator("#fill")).toBeDisabled(); await expect(f.popup.locator("#assistance-fill")).toBeDisabled();
    // Reopening reads the fresh empty namespace; it does not start a connection.
    await f.popup.reload(); await expect(f.popup.locator("#native-state")).toContainText("Ready to start");
    await expect(f.popup.locator("#assistance")).toBeHidden();
    assert.equal((await f.popup.locator("body").textContent()).includes("retained-old-application"), false);
    assert.equal((await f.worker.evaluate(async () => chrome.storage.local.get(null))).pairing_checkpoint.marker, "retained-old-connection");
    assert.deepEqual(f.requests, []);
    // A stale cached engine is fenced at the action boundary when legacy state drifts.
    await f.worker.evaluate(() => chrome.storage.local.set({ native_application_evidence: { changed: true } }));
    const reply = await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_start" }));
    assert.equal(reply.ok, false); assert.match(reply.error, /recovery needs inspection/);
    assert.equal(await f.probe.evaluate(async () => (await new (await import(chrome.runtime.getURL("lost-key-recovery.js"))).RecoveryJournal().read()).record.phase), "blocked");
    assert.deepEqual(f.requests, []);
  } finally { await f.close(); }
});

test("actual IDB simultaneous confirmed commits reserve one archive and generate one retained key only", async () => {
  const f = await fixture();
  try {
    const result = await f.probe.evaluate(async () => {
      const { LostKeyRecovery, RecoveryJournal } = await import(chrome.runtime.getURL("lost-key-recovery.js"));
      const { createLocalIdentity } = await import(chrome.runtime.getURL("device.js"));
      let generated = 0;
      const make = () => new LostKeyRecovery({ readLegacy: () => chrome.storage.local.get(null), createIdentity: async () => { generated++; return createLocalIdentity(); } });
      const a = make(), b = make(), [one, two] = await Promise.all([a.review(), b.review()]);
      const results = await Promise.allSettled([a.commit({ recovery_id: one.recovery_id, acknowledged: true }), b.commit({ recovery_id: two.recovery_id, acknowledged: true })]);
      const { record, legacyMissing } = await new RecoveryJournal().read();
      return { fulfilled: results.filter((value) => value.status === "fulfilled").length, generated, record, legacyMissing, saved: await chrome.storage.local.get(null) };
    });
    assert.equal(result.fulfilled, 1); assert.equal(result.generated, 1); assert.equal(result.record.phase, "active");
    assert.equal(result.legacyMissing, true); assert.deepEqual(result.record.snapshot, old); assert.deepEqual(result.saved, old);
    assert.deepEqual(f.requests, []);
  } finally { await f.close(); }
});

test("actual pending recovery is read-only after interruption and cannot regenerate, start or inherit old approvals", async () => {
  const f = await fixture();
  try {
    await f.probe.evaluate(async () => {
      const { LostKeyRecovery, RecoveryJournal } = await import(chrome.runtime.getURL("lost-key-recovery.js"));
      const journal = new RecoveryJournal(); journal.activate = async () => { throw new Error("owned synthetic activation interruption"); };
      const recovery = new LostKeyRecovery({ journal, readLegacy: () => chrome.storage.local.get(null) });
      const review = await recovery.review();
      try { await recovery.commit({ recovery_id: review.recovery_id, acknowledged: true }); } catch { /* expected pending record */ }
    });
    await f.popup.goto(`chrome-extension://${new URL(f.worker.url()).host}/popup.html`);
    await expect(f.popup.locator("#error")).toContainText("HW-COMPANION-START-RECOVERY");
    await expect(f.popup.locator("#recovery-review")).toBeDisabled();
    await f.popup.locator("#recovery-inspect").click();
    await expect(f.popup.locator("#recovery-summary")).toContainText("pending");
    await expect(f.popup.locator("#recovery-confirmation")).toBeHidden();
    const reply = await f.popup.evaluate(() => chrome.runtime.sendMessage({ type: "lost_key_review" }));
    assert.equal(reply.ok, false); await expect(f.popup.locator("#assistance-fill")).toBeDisabled();
    assert.deepEqual(await f.worker.evaluate(async () => chrome.storage.local.get(null)), old);
    assert.deepEqual(f.requests, []);
  } finally { await f.close(); }
});
