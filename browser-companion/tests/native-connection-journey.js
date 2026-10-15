// Reusable composed journey. Caller owns real PG/native fixture + NextAuth TLS
// processes; this module owns only its temporary actual Chromium MV3 profile.
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createHash, X509Certificate, randomUUID } from "node:crypto";
import { chromium, expect } from "@playwright/test";
import { nativeFixtureBuild } from "../scripts/native-fixture-build.js";

export async function nativeConnectionJourney({ origin, fixtureDirectory, manifestIdentity, executorRevision }) {
  const parsed = new URL(origin);
  if (parsed.origin !== origin || parsed.protocol !== "https:" || !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname)) throw new Error("Owned HTTPS loopback fixture required");
  const metadata = JSON.parse(await readFile(`${fixtureDirectory}/metadata.json`, "utf8"));
  if (metadata.candidate_identities_seeded !== 0 || metadata.actual_postgresql !== true || metadata.actual_native_v3 !== true
      || metadata.extension_id !== manifestIdentity.extensionId || metadata.executor_revision !== executorRevision
      || metadata.claim_issuer !== "hirewiz-pairing") throw new Error("Exact genuine candidate/native fixture metadata required");
  const certificate = new X509Certificate(await readFile(`${fixtureDirectory}/cert.pem`));
  const spki = createHash("sha256").update(certificate.publicKey.export({ type: "spki", format: "der" })).digest("base64");
  const directory = await mkdtemp(`${tmpdir()}/hirewiz-native-connection-profile-`);
  const build = await nativeFixtureBuild(`${directory}/extension`, { websiteOrigin: origin, authorityKey: metadata.public_claim_jwk,
    executorRevision, manifestKey: manifestIdentity.manifestKey, grantTestOrigin: true });
  assert.equal(build.extensionId, manifestIdentity.extensionId);
  const checks = [], requests = [];
  let context, checkpoint = "Chrome startup";
  const check = (name, condition) => { assert.ok(condition, name); checks.push(name); };
  const launch = async () => {
    context = await chromium.launchPersistentContext(`${directory}/profile`, { channel: "chromium", headless: true,
      args: [`--disable-extensions-except=${directory}/extension`, `--load-extension=${directory}/extension`, `--ignore-certificate-errors-spki-list=${spki}`] });
    // Path/status/method only. Request headers, bodies and credentials are never
    // retained, even when a real socket/native checkpoint fails.
    context.on("request", (request) => { const url = new URL(request.url()); if (url.origin === origin && url.pathname.startsWith("/api/")) requests.push({ path: url.pathname, method: request.method() }); });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    check("actual MV3 ID matches operator-registered fixture identity", new URL(worker.url()).host === manifestIdentity.extensionId);
    const popup = await context.newPage(); await popup.goto(`chrome-extension://${manifestIdentity.extensionId}/popup.html`);
    await expect(popup.locator("#mode")).toContainText("Controlled local native connection test");
    return { worker, popup };
  };
  const password = `owned-synthetic-${randomUUID()}`;
  const email = `native-mv3-${randomUUID()}@example.com`;
  try {
    let { worker, popup } = await launch();
    const permissions = await popup.evaluate(() => chrome.permissions.getAll());
    check("test manifest grants only the exact HTTPS loopback website", permissions.origins.length === 1 && permissions.origins[0] === `${origin}/*` && permissions.permissions.join(",") === "storage");
    checkpoint = "native prepare/create via actual MV3 handler";
    await popup.locator("#native-start").click();
    await expect(popup.locator("#native-state")).toContainText("Compare both fingerprints", { timeout: 30000 });
    const initial = await popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_status" }));
    check("native request returns public review fingerprints only", initial.ok && initial.value.phase === "awaiting_candidate" && initial.value.allowed_actions.length === 0);
    const newPage = context.waitForEvent("page"); await popup.locator("#native-review").click(); const site = await newPage;
    await site.waitForURL(initial.value.review_url);
    checkpoint = "unauthenticated website handoff";
    await site.getByRole("button", { name: "Review connection", exact: true }).click();
    const signIn = site.getByRole("link", { name: "Sign in and return to this connection" }); await expect(signIn).toBeVisible();
    const returnPath = `/browser-companion?pairing=${initial.value.pairing_id}`;
    check("website preserves exact pairing during sign-in handoff", await signIn.getAttribute("href") === `/login?returnTo=${encodeURIComponent(returnPath)}`);
    await signIn.click(); await site.locator('[data-auth-ready="true"]').waitFor();
    checkpoint = "genuine fresh candidate registration/native issuance/protected activation";
    await site.goto(`${origin}/register?returnTo=${encodeURIComponent(returnPath)}`);
    await site.locator('[data-auth-ready="true"]').waitFor();
    const native = site.getByRole("checkbox", { name: /Create a password account eligible/ }); await native.waitFor();
    await site.getByLabel("Email address").fill(email); await site.getByLabel("Password", { exact: true }).fill(password);
    await site.getByRole("checkbox", { name: /I am at least 18/ }).check(); await native.check();
    await site.getByRole("button", { name: "Create account", exact: false }).click();
    await site.waitForURL(`${origin}${returnPath}`, { timeout: 30000 });
    const session = await site.evaluate(async () => (await fetch("/api/auth/session")).json());
    check("real registration/login creates an actual NextAuth session", Boolean(session.user?.id));
    check("public session does not expose private native context or bearer", !/browserPairingSession|account_binding_id|session_id|accessToken|candidateLogoutRequest/.test(JSON.stringify(session)));
    checkpoint = "same retained candidate challenge through BFF/native protected join";
    await site.getByRole("button", { name: "Review connection", exact: true }).click();
    await expect(site.getByRole("button", { name: "Confirm this device", exact: true })).toBeVisible({ timeout: 30000 });
    const webHashes = await site.locator("dl dd").allTextContents();
    check("website and actual MV3 display the exact same native request/key hashes", webHashes.length === 2 && webHashes[0] === initial.value.key_sha256 && webHashes[1] === initial.value.request_sha256);
    await site.getByLabel("HireWiz password").fill(password); await site.getByRole("checkbox", { name: /I checked that these fingerprints match/ }).check();
    checkpoint = "actual password verifier/protected consuming approval/native confirmation";
    await site.getByRole("button", { name: "Confirm this device", exact: true }).click();
    await expect(site.getByRole("status")).toContainText("Candidate confirmation saved", { timeout: 30000 });
    check("website clears password input after native approval", await site.getByLabel("HireWiz password").count() === 0);
    checkpoint = "actual WebCrypto challenge proof/native signed identity claim";
    await popup.locator("#native-confirmed").check(); await popup.locator("#native-complete").click();
    await expect(popup.locator("#native-state")).toContainText("Current browser identity verified", { timeout: 30000 });
    const paired = await popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_status" }));
    check("actual device proof establishes identity with zero application grants", paired.ok && paired.value.connected && paired.value.allowed_actions.length === 0);
    for (const type of ["prepare", "fill", "upload", "submit"]) check(`identity cannot authorize ${type}`, !(await popup.evaluate((operation) => chrome.runtime.sendMessage({ type: operation }), type)).ok);
    const retained = await worker.evaluate(async () => (await chrome.storage.local.get("pairing_checkpoint")).pairing_checkpoint);
    check("durable checkpoint excludes claims/credentials/session context", retained.phase === "paired" && JSON.stringify(retained).length < 2048 && !/password|cookie|csrf|assertion|claim|session_id/.test(JSON.stringify(retained)));
    checkpoint = "explicit online refresh";
    await popup.locator("#native-refresh").click(); await expect(popup.locator("#native-state")).toContainText("Current browser identity verified");
    checkpoint = "full Chrome restart retains identity but no cached authority";
    await context.close(); ({ worker, popup } = await launch());
    await expect(popup.locator("#native-state")).toContainText("Connection remembered");
    const remembered = await popup.evaluate(() => chrome.runtime.sendMessage({ type: "pairing_status" }));
    check("restart drops cached claim without starting a connection", remembered.value.phase === "paired" && remembered.value.connected === false);
    await popup.locator("#native-refresh").click(); await expect(popup.locator("#native-state")).toContainText("Current browser identity verified");
    const restarted = await worker.evaluate(async () => (await chrome.storage.local.get("pairing_checkpoint")).pairing_checkpoint);
    check("actual native refresh preserves exact device/key owner", restarted.device_id === retained.device_id && restarted.key_sha256 === retained.key_sha256);
    check("identity journey calls no application billing, employer, fill/upload/submit API", requests.every((request) => !/billing|application.*(?:fill|upload|submit)|employer/.test(request.path)));
    await writeFile(`${fixtureDirectory}/mv3-connection-report.json`, JSON.stringify({ status: "PASS", checks, scope: "actual Chromium MV3 + HTTPS NextAuth/BFF + PostgreSQL/native protected lifecycle; synthetic external custody", production_enabled: false, native_permission_dialog_approved: false }, null, 2));
    return { status: "PASS", checks };
  } catch {
    await writeFile(`${fixtureDirectory}/mv3-connection-report.json`, JSON.stringify({ status: "FAIL", checkpoint, checks, requests, scope: "actual composed first-connection acceptance attempt; no secret/body/header capture" }, null, 2));
    throw new Error(`Actual native MV3 connection checkpoint failed: ${checkpoint}`);
  } finally {
    await context?.close(); await rm(directory, { recursive: true, force: true });
  }
}
