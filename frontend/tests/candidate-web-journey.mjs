// Actual cold NextAuth/browser path. All identities/passwords are synthetic.
import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const origin = process.env.BROWSER_PAIRING_WEBSITE_ORIGIN;
const directory = process.env.HIREWIZ_CANDIDATE_FIXTURE_DIRECTORY;
const checks = [];
let checkpoint = "startup";
const replies = [];
const apiRequests = [];
let externalRequestsBlocked = 0;
const browser = await chromium.launch({ headless: true });
const contexts = [];
function check(name, condition) { if (!condition) throw new Error(name); checks.push(name); }
async function context() {
  const value = await browser.newContext({ ignoreHTTPSErrors: true });
  await value.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { externalRequestsBlocked++; await route.abort(); return; }
    if (url.pathname.startsWith("/api/")) apiRequests.push({ path: url.pathname, method: request.method() });
    await route.continue();
  });
  contexts.push(value);
  return value;
}
const password = "synthetic-cold-web-password-20261009";
const id = process.env.HIREWIZ_CANDIDATE_TEST_ID;
async function register(page, email, native = true) {
  checkpoint = "registration page hydration";
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === origin && url.pathname.startsWith("/api/")) replies.push({ path: url.pathname, status: response.status() });
  });
  await page.goto(origin + "/register");
  await page.locator('[data-auth-ready="true"]').waitFor();
  // Capability appears only after client hydration and the actual BFF read.
  await page.getByRole("checkbox", { name: /Create a password account eligible/ }).waitFor();
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("checkbox", { name: /I am at least 18/ }).check();
  if (native) await page.getByRole("checkbox", { name: /Create a password account eligible/ }).check();
  checkpoint = "registration submit enabled";
  check("registration submit is enabled after consent", await page.getByRole("button", { name: "Create account" }).isEnabled());
  checkpoint = "registration submit";
  await page.getByRole("button", { name: "Create account" }).click();
}
async function publicSession(page) {
  return await page.evaluate(async () => (await fetch("/api/auth/session")).json());
}
async function eligibility(page) {
  return await page.evaluate(async () => (await fetch("/api/candidate-account/eligibility")).json());
}
try {
  const a = await context(), b = await context();
  const page = await a.newPage(), other = await b.newPage();
  await register(page, `native-a-${id}@example.com`);
  await page.waitForURL("**/resume", { timeout: 30000 });
  const session = await publicSession(page);
  check("genuine native signup and actual NextAuth login", Boolean(session.user?.id));
  check("public session never exposes native context, bearer or logout request",
    !/browserPairingSession|account_binding_id|session_id|accessToken|candidateLogoutRequest/.test(JSON.stringify(session)));
  check("actual protected eligibility succeeds", (await eligibility(page)).status === "ELIGIBLE");
  await register(other, `native-b-${id}@example.com`);
  await other.waitForURL("**/resume", { timeout: 30000 });
  const otherSession = await publicSession(other);
  check("independent candidate account uses separate actual session", otherSession.user.id !== session.user.id);
  checkpoint = "generic auth issuer and denial bypass refusal";
  const blockedPaths = ["auth/login", "%61uth/%6cogin", "auth/login/", "auth/x/../login", "auth/%2e/login",
    "auth/candidate/v1/login", "%61uth/candidate/v1/%6cogin", "auth/candidate/v1/login/",
    "auth/google-login", "auth/candidate/v1/register", "auth/candidate/v1/password", "auth/candidate/v1/web-logout",
    "auth/candidate/v1/registration-status", "public/%2e%2e/auth/login", "%2561uth/login"];
  for (const suffix of blockedPaths) {
    const result = await page.evaluate(async ({suffix,email,password}) => {
      const response=await fetch(`/api/backend/${suffix}`, {method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({email,password,operation_id:crypto.randomUUID()})});
      return {status:response.status,body:await response.text()};
    }, {suffix,email:`native-b-${id}@example.com`,password});
    check(`generic auth transport denies ${suffix}`, result.status>=400 && !/access_token|browser_pairing_session/.test(result.body));
  }
  check("foreign client logout UUID never replaces private retained request", (await eligibility(page)).status === "ELIGIBLE");
  const me=await page.evaluate(async()=>await(await fetch("/api/backend/auth/me")).json());
  check("explicit safe me alias retains the same server-owned account", me.id===Number(session.user.id));
  checkpoint = "retained account switch";
  await page.goto(origin + "/login");
  await page.locator('[data-auth-ready="true"]').waitFor();
  await page.getByLabel("Email address").fill(`native-b-${id}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Sign out of the current account" }).waitFor();
  check("actual NextAuth account switch refuses retained cookie replacement", (await publicSession(page)).user.id === session.user.id);

  checkpoint = "unknown no-persist logout";
  const savedCookies = await a.cookies();
  fs.writeFileSync(path.join(directory, "logout-unknown"), "unpersisted");
  await page.goto(origin + "/logout");
  await page.getByRole("alert").filter({ hasText: "Secure sign-out is pending" }).waitFor();
  check("unknown unpersisted logout preserves encrypted cookie", (await publicSession(page)).user.id === session.user.id);
  check("unknown no-persist does not claim revocation", (await eligibility(page)).status === "ELIGIBLE");
  await page.getByRole("button", { name: "Retry sign-out" }).click();
  await page.waitForURL("**/login", { timeout: 30000 });
  check("actual retained denial precedes successful cookie clearing", !(await publicSession(page)).user);
  const restored = await context();
  await restored.addCookies(savedCookies);
  const stale = await restored.newPage();
  await stale.goto(origin + "/login");
  check("restored stale encrypted cookie cannot authorize protected session", (await eligibility(stale)).status === "PASSWORD_SIGN_IN_REQUIRED");
  check("unrelated candidate remains usable after logout", (await eligibility(other)).status === "ELIGIBLE");

  checkpoint = "unknown persisted logout";
  fs.writeFileSync(path.join(directory, "logout-unknown"), "persisted");
  await other.goto(origin + "/logout");
  await other.getByRole("alert").filter({ hasText: "Secure sign-out is pending" }).waitFor();
  check("unknown persisted logout keeps cookie until explicit confirmation", (await publicSession(other)).user.id === otherSession.user.id);
  check("persisted unknown pending denial already blocks actions", (await eligibility(other)).status === "PASSWORD_SIGN_IN_REQUIRED");
  await other.getByRole("button", { name: "Retry sign-out" }).click();
  await other.waitForURL("**/login", { timeout: 30000 });
  check("same private request confirms only retained denial and clears cookie", !(await publicSession(other)).user);

  const pendingContext = await context(), pending = await pendingContext.newPage();
  fs.writeFileSync(path.join(directory, "activation-unknown"), "unpersisted");
  await register(pending, `pending-${id}@example.com`);
  await pending.getByRole("alert").filter({ hasText: "Check its status" }).waitFor();
  await pending.getByRole("button", { name: "Check registration status", exact: true }).click();
  await pending.getByRole("alert").filter({ hasText: "Registration is pending review" }).waitFor();
  check("unknown genuine enrollment is recoverable as read-only PENDING", !(await publicSession(pending)).user);
  checkpoint = "pending registration login refusal";
  await pending.goto(origin + "/login");
  await pending.locator('[data-auth-ready="true"]').waitFor();
  await pending.getByLabel("Email address").fill(`pending-${id}@example.com`);
  await pending.getByLabel("Password", { exact: true }).fill(password);
  await pending.getByRole("button", { name: "Sign in", exact: true }).click();
  await pending.getByRole("alert").waitFor();
  check("pending native registration cannot use legacy credential login", !(await publicSession(pending)).user);

  const legacyContext = await context(), legacy = await legacyContext.newPage();
  await register(legacy, `legacy-${id}@example.com`, false);
  await legacy.waitForURL("**/resume", { timeout: 30000 });
  check("ordinary signup and legacy login stay available", Boolean((await publicSession(legacy)).user?.id));
  check("legacy account never receives synthetic native pairing claims", (await eligibility(legacy)).status === "LEGACY_ENROLLMENT_UNAVAILABLE");
  await legacy.setViewportSize({ width: 390, height: 844 });
  await legacy.goto(origin + "/login");
  check("mobile auth layout has no horizontal overflow", await legacy.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await legacy.screenshot({ path: path.join(directory, "mobile-login.png") });
  checkpoint = "dedicated profile edit and account export";
  await legacy.goto(origin+"/profile");
  await legacy.getByLabel("Full name",{exact:true}).fill("Synthetic Professional Candidate");
  await legacy.getByRole("button",{name:"Save profile"}).click();
  await legacy.getByText("Saved",{exact:true}).waitFor();
  const updated=await legacy.evaluate(async()=>await(await fetch("/api/account/profile")).json());
  check("dedicated authenticated profile mutation preserves ordinary workspace",updated.full_name==="Synthetic Professional Candidate");
  const downloadPromise=legacy.waitForEvent("download");
  await legacy.getByRole("button",{name:"Export JSON"}).click();
  const download=await downloadPromise, stream=await download.createReadStream();
  let exported="";for await(const chunk of stream) exported+=chunk.toString();
  const payload=JSON.parse(exported);
  check("actual user account export retains data without bearer/native context",payload.account.id===Number((await publicSession(legacy)).user.id)
    && !/access_token|browser_pairing_session|candidate_lifetime/.test(exported));
  checkpoint = "native account deletion then protected cookie confirmation";
  const deletedContext=await context(),deleted=await deletedContext.newPage();
  await register(deleted,`native-delete-${id}@example.com`);await deleted.waitForURL("**/resume");
  await deleted.goto(origin+"/profile");await deleted.getByRole("button",{name:"Delete account",exact:true}).click();
  await deleted.getByRole("button",{name:"Permanently delete",exact:true}).click();await deleted.waitForURL("**/register");
  check("actual native account deletion retains tombstone before cookie clearing",!(await publicSession(deleted)).user);
  const metadata = JSON.parse(fs.readFileSync(path.join(directory, "metadata.json")));
  check("fixture forbids ambient credentials, secure cloud transport and external HTTP",
    metadata.ambient_credentials_forbidden && metadata.secure_cloud_transport_forbidden && metadata.external_http_forbidden);
  check("browser only uses the exact loopback origin", externalRequestsBlocked === 0);
  check("no employer or AI mutation requested", apiRequests.every(({ path, method }) =>
    method === "GET" || ["/api/candidate-account/register", "/api/candidate-account/registration-status",
      "/api/auth/callback/credentials", "/api/auth/signout", "/api/account/register", "/api/account/profile", "/api/account/delete"].includes(path)
      || path.startsWith("/api/backend/") && blockedPaths.some(suffix=>new URL(`/api/backend/${suffix}`,origin).pathname===path)));
  fs.writeFileSync(path.join(directory, "browser-report.json"), JSON.stringify({ status: "PASS", checks, synthetic_identities: true }, null, 2));
} catch {
  // No raw page/body/header/credential output. Exact failing checkpoint only.
  const pages = contexts.flatMap((value) => value.pages());
  const forms = [];
  for (const page of pages) {
    forms.push({ path: new URL(page.url()).pathname,
      buttons: await page.getByRole("button").evaluateAll((nodes) => nodes.map((node) => ({ disabled: node.disabled, label: node.textContent?.slice(0, 80) }))),
      checkboxes: await page.getByRole("checkbox").evaluateAll((nodes) => nodes.map((node) => ({ checked: node.checked }))) });
  }
  fs.writeFileSync(path.join(directory, "browser-report.json"), JSON.stringify({ status: "FAIL", checks, checkpoint, replies, forms }, null, 2));
  process.exitCode = 1;
} finally {
  for (const value of contexts) await value.close();
  await browser.close();
}
