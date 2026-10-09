// @vitest-environment node
import { createHmac } from "node:crypto";
import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CSRF_COOKIE } from "../../../../../lib/browserPairingGateway";
import { POST } from "./route";

const site = "https://www.hirewizhq.com";
const secret = "synthetic-nextauth-secret-for-http-tests-only";
const gatewayKey = "a1".repeat(32);
const retained = { candidate_id: 7, account_binding_id: "00000000-0000-4000-8000-000000000001", session_id: "00000000-0000-4000-8000-000000000002" };
const fetchBackend = vi.fn();
const context = (operation: string) => ({ params: Promise.resolve({ operation }) });

async function request(operation: string, extra: Record<string, string> = {}, session: unknown = retained,
                       body = "{}", tokenPresent = true) {
  const jwt = await encode({ secret, token: { browserPairingSession: session, accessToken: "synthetic-legacy-bearer", hirewizUserId: 7 }, maxAge: 60 });
  return new NextRequest(`${site}/api/browser-pairing/candidate/${operation}`, { method: "POST",
    headers: { host: "www.hirewizhq.com", origin: site, "sec-fetch-site": "same-origin", "content-type": "application/json",
      ...(tokenPresent ? { cookie: `__Secure-next-auth.session-token=${jwt}` } : {}), ...extra }, body });
}

async function csrf(session: unknown = retained) {
  const response = await POST(await request("csrf", {}, session), context("csrf"));
  expect(response.status).toBe(200);
  const data = await response.json();
  return { token: data.csrf_token as string, cookie: response.cookies.get(CSRF_COOKIE)!.value, response };
}

async function withCsrf(operation: string, issued: Awaited<ReturnType<typeof csrf>>, extra: Record<string, string> = {}, session: unknown = retained, body = "{}") {
  const incoming = await request(operation, {}, session, body);
  const cookie = `${incoming.headers.get("cookie")}; ${CSRF_COOKIE}=${issued.cookie}`;
  return request(operation, { cookie, "x-hirewiz-csrf": issued.token, ...extra }, session, body);
}

beforeEach(() => {
  vi.stubEnv("NEXTAUTH_SECRET", secret);
  vi.stubEnv("BROWSER_PAIRING_GATEWAY_SECRET", gatewayKey);
  vi.stubEnv("BROWSER_PAIRING_WEBSITE_ORIGIN", site);
  vi.stubEnv("BACKEND_URL", "https://native-backend.example.test");
  vi.stubGlobal("fetch", fetchBackend);
  fetchBackend.mockImplementation(async () => Response.json({ status: "CANDIDATE_CONFIRMED", pairing_id: "00000000-0000-4000-8000-000000000003", device_id: "00000000-0000-4000-8000-000000000004" }));
});
afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("candidate pairing BFF uses real encrypted NextAuth JWTs", () => {
  it("issues a Secure HttpOnly host-only Strict CSRF cookie after retained server-session authentication", async () => {
    const issued = await csrf();
    const cookie = issued.response.cookies.get(CSRF_COOKIE)!;
    expect(cookie).toMatchObject({ secure: true, httpOnly: true, sameSite: "strict", path: "/", maxAge: 600 });
    expect(cookie.domain).toBeUndefined();
    expect(issued.response.headers.get("cache-control")).toContain("no-store");
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("binds the exact raw password body/path/retained context to a short signed server request", async () => {
    const issued = await csrf();
    const raw = '{ "password": "synthetic-http-password", "confirmed": true }';
    const response = await POST(await withCsrf("confirm", issued, {}, retained, raw), context("confirm"));
    expect(response.status).toBe(200);
    const [target, options] = fetchBackend.mock.calls[0];
    expect(target).toBe("https://native-backend.example.test/api/v1/browser-pairing/candidate/confirm");
    expect(options.credentials).toBe("omit");
    expect(options.redirect).toBe("error");
    expect(options.headers.Authorization).toBeUndefined();
    expect(options.headers.Cookie).toBeUndefined();
    expect(new TextDecoder().decode(options.body)).toBe(raw);
    const bytes = Buffer.from(options.headers["X-Hirewiz-Gateway-Assertion"], "base64url");
    const payload = JSON.parse(bytes.toString("utf8"));
    expect(payload.context).toEqual(retained);
    expect(payload.path).toBe("/api/v1/browser-pairing/candidate/confirm");
    expect(payload.expires_at_ms - payload.issued_at_ms).toBe(5000);
    expect(payload.audience).toBe("hirewiz:browser-pairing-candidate");
    expect(options.headers["X-Hirewiz-Gateway-Signature"]).toBe(createHmac("sha256", Buffer.from(gatewayKey, "hex")).update("hirewiz.browser-gateway.v1\n").update(bytes).digest("hex"));
    expect(JSON.stringify(payload)).not.toContain("synthetic-http-password");
    expect(await response.json()).toEqual({ status: "CANDIDATE_CONFIRMED", pairing_id: "00000000-0000-4000-8000-000000000003", device_id: "00000000-0000-4000-8000-000000000004" });
  });
  it.each([undefined, { candidate_id: 7 }, { ...retained, candidate_id: "7" }, { ...retained, untrusted: true }])("rejects absent or malformed retained context despite valid legacy bearer", async (session) => {
    const response = await POST(await request("csrf", {}, session ?? null), context("csrf"));
    expect(response.status).toBe(503);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("rejects a missing real authentication cookie", async () => {
    expect((await POST(await request("csrf", {}, retained, "{}", false), context("csrf"))).status).toBe(401);
  });
  it.each(["https://attacker.example", "https://app.hirewizhq.com", "null", ""]) ("rejects foreign, sibling, opaque and absent origins before forwarding", async (origin) => {
    expect((await POST(await request("csrf", { origin }), context("csrf"))).status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it.each(["missing", "changed", "session", "subdomain-cookie"]) ("rejects %s CSRF input", async (fault) => {
    const issued = await csrf();
    let extra: Record<string, string> = {}, session = retained;
    if (fault === "missing") extra = { "x-hirewiz-csrf": "" };
    if (fault === "changed") extra = { "x-hirewiz-csrf": "0".repeat(64) };
    if (fault === "session") session = { ...retained, session_id: "00000000-0000-4000-8000-000000000003" };
    if (fault === "subdomain-cookie") issued.cookie = `${issued.token}.${Date.now() + 600000}.${"0".repeat(64)}`;
    expect((await POST(await withCsrf("challenge", issued, extra, session), context("challenge"))).status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("returns a bounded unavailable response on missing gateway configuration", async () => {
    vi.stubEnv("BROWSER_PAIRING_GATEWAY_SECRET", "");
    expect((await POST(await request("csrf"), context("csrf"))).status).toBe(503);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("withholds untrusted backend response fields and exception text", async () => {
    const issued = await csrf();
    fetchBackend.mockImplementationOnce(async () => Response.json({ status: "CANDIDATE_CONFIRMED", password: "synthetic-untrusted-body", session_id: retained.session_id }));
    const response = await POST(await withCsrf("confirm", issued), context("confirm"));
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("synthetic-untrusted-body");
    fetchBackend.mockImplementationOnce(async () => new Response("synthetic-private-error", { status: 403 }));
    const rejected = await POST(await withCsrf("confirm", issued), context("confirm"));
    expect(rejected.status).toBe(403);
    expect(await rejected.text()).not.toContain("synthetic-private-error");
  });
  it.each([[403, "never"], [503, "never"], [403, "reject"], [503, "reject"]] as const)("preserves upstream %s when cancellation will %s", async (status, cancellation) => {
    const issued = await csrf();
    const incoming = await withCsrf("confirm", issued);
    let cancelled = false, timer: ReturnType<typeof setTimeout> | undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode("synthetic-private-backend-error")); },
      cancel() {
        cancelled = true;
        return cancellation === "never" ? new Promise<void>(() => undefined) : Promise.reject(new Error("Owned cancellation refusal"));
      },
    });
    fetchBackend.mockResolvedValueOnce(new Response(body, { status }));
    const response = await Promise.race([
      POST(incoming, context("confirm")),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("Pairing error response did not settle")), 250); }),
    ]).finally(() => clearTimeout(timer));
    expect(cancelled).toBe(true);
    expect(body.locked).toBe(false);
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ detail: status === 403 ? "Browser pairing request was rejected" : "Browser pairing is currently unavailable" });
    expect(response.headers.get("cache-control")).toContain("no-store");
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
});
