// @vitest-environment node
import { createHash } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { guardAuthRequest, revokeRetainedToken } from "./candidateAuthServer";

const origin = "https://candidate.example.com";
const secret = "synthetic-web-auth-secret-that-is-only-a-test";
const context = { candidate_id: 12, account_binding_id: "01234567-89ab-cdef-0123-456789abcdef",
  session_id: "11234567-89ab-cdef-0123-456789abcdef" };
const operation = "21234567-89ab-cdef-0123-456789abcdef";
const csrf = "a".repeat(64);
beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubEnv("NEXTAUTH_URL", origin);
  vi.stubEnv("NEXTAUTH_SECRET", secret);
  vi.stubEnv("BACKEND_URL", "https://backend.example.com"); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", "6e".repeat(32)); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "fixture_ingress_v1");
});

async function request(path = "signout", options: { legacy?: boolean; origin?: string; body?: string } = {}) {
  const token = { hirewizUserId: 12, accessToken: "synthetic-server-only-bearer",
    ...(options.legacy ? {} : { browserPairingSession: context, candidateLogoutRequest: operation }) };
  const encoded = await encode({ token, secret });
  const cookie = `${csrf}|${createHash("sha256").update(csrf + secret).digest("hex")}`;
  return new NextRequest(`${origin}/api/auth/${path}`, { method: "POST",
    headers: { origin: options.origin || origin, "Content-Type": "application/x-www-form-urlencoded",
      cookie: `__Secure-next-auth.session-token=${encoded}; __Host-next-auth.csrf-token=${encodeURIComponent(cookie)}` },
    body: options.body || new URLSearchParams({ csrfToken: csrf, callbackUrl: "/login", json: "true" }).toString() });
}

describe("actual encrypted cookie pre-handler signout guard", () => {
  it("uses the same trimmed configured secret as the real NextAuth issuer", async () => {
    vi.stubEnv("NEXTAUTH_SECRET", ` ${secret} `);
    const fetch = vi.fn(async () => Response.json({status:"COOKIE_DENIAL_RETAINED"})); vi.stubGlobal("fetch",fetch);
    const result = await guardAuthRequest(await request(), "signout");
    expect(result.request).toBeDefined(); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("requires exact retained denial before permitting the NextAuth handler", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ status: "COOKIE_DENIAL_RETAINED" })));
    vi.stubGlobal("fetch", fetch);
    const result = await guardAuthRequest(await request(), "signout");
    expect(result.response).toBeUndefined();
    expect(result.request).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://backend.example.com/api/auth/candidate/v1/web-logout");
    expect(JSON.parse(init.body as string)).toEqual({ operation_id: operation });
    expect(JSON.stringify(init.body)).not.toContain(context.session_id);
  });
  it.each([503, 401, 500])("keeps cookie unchanged when backend returns %s", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "private-secret-must-not-be-reflected" }), { status })));
    const result = await guardAuthRequest(await request(), "signout");
    expect(result.request).toBeUndefined();
    expect(result.response?.status).toBe(503);
    expect(result.response?.headers.get("set-cookie")).toBeNull();
    expect(await result.response?.text()).not.toContain("private-secret");
  });
  it("preserves legacy logout without native calls or manufactured context", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const result = await guardAuthRequest(await request("signout", { legacy: true }), "signout");
    expect(result.request).toBeDefined();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("preserves the actual NextAuth client redirect=false credentials form", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const result = await guardAuthRequest(await request("callback/credentials", { legacy: true,
      body: new URLSearchParams({ csrfToken: csrf, callbackUrl: "/login", json: "true", redirect: "false",
        email: "synthetic@example.com", password: "synthetic-only-password" }).toString() }), "callback", "credentials");
    expect(result.request).toBeDefined();
    expect(result.response).toBeUndefined();
  });
  it("blocks credentials and Google account switching before any issuer call", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const result = await guardAuthRequest(await request("callback/credentials"), "callback", "credentials");
    expect(result.response?.status).toBe(409);
    expect(await result.response?.text()).toContain("SESSION_LOGOUT_REQUIRED");
    const google = await guardAuthRequest(await request("signin/google"), "signin", "google");
    expect(google.response?.status).toBe(409);
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    { origin: "https://attacker.example.com" }, { body: `csrfToken=${csrf}&csrfToken=${csrf}&json=true` },
    { body: `csrfToken=${"b".repeat(64)}&json=true` }, { body: `csrfToken=${csrf}&password=private-input&json=true` },
    { body: "x".repeat(8193) },
  ])("rejects origin, CSRF, duplicate, unknown or oversized form without backend call", async (value) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const result = await guardAuthRequest(await request("signout", value), "signout");
    expect(result.request).toBeUndefined();
    expect(result.response?.headers.get("set-cookie")).toBeNull();
    expect(await result.response?.text()).not.toContain("private-input");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects a missing private original logout request", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect(await revokeRetainedToken({ hirewizUserId: 12, accessToken: "synthetic", browserPairingSession: context })).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });
});
