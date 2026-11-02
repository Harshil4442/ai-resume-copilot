// @vitest-environment node
import { createHash } from "node:crypto";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as route from "./route";

const origin = "https://website.example.invalid", secret = "synthetic-nextauth-private-secret";
const csrf = "c".repeat(64), token = "E".repeat(43);
let scope = 0;
beforeEach(() => {
  vi.restoreAllMocks(); vi.stubEnv("NEXTAUTH_URL", origin); vi.stubEnv("NEXTAUTH_SECRET", secret);
  vi.stubEnv("BACKEND_URL", "https://backend.example.invalid");
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", "6e".repeat(32));
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", `erasure_fixture_${++scope}`);
});
function request(operation: string, body: string, extra: Record<string, string> = {}, query = "") {
  return new NextRequest(`${origin}/api/privacy-erasure/${operation}${query}`, { method: "POST", body,
    headers: { origin, "content-type": "application/json", "x-hirewiz-account-csrf": csrf,
      cookie: `__Host-next-auth.csrf-token=${encodeURIComponent(csrf + "|" + createHash("sha256").update(csrf + secret).digest("hex"))}; __Secure-next-auth.session-token=not-forwarded`, ...extra } });
}
const context = (operation: string) => ({ params: Promise.resolve({ operation }) });
function backend(data: unknown, status: number) {
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    void init;
    return String(url).endsWith("/availability")
      ? Response.json({ fresh_registration: true, legacy_enrollment: true }) : Response.json(data, { status });
  });
  vi.stubGlobal("fetch", fetch); return fetch;
}

describe("purpose-bound public erasure BFF", () => {
  it.each(["request", "confirm"])("forwards one exact %s POST with private ingress and no bearer/cookies", async (operation) => {
    const body = operation === "request" ? { email: "synthetic@example.invalid" } : { token, confirm_erasure: true };
    const code = operation === "request" ? "ERASURE_LINK_REQUESTED" : "ACCOUNT_ERASED", status = operation === "request" ? 202 : 200;
    const fetch = backend({ status: code }, status);
    const response = await route.POST(request(operation, JSON.stringify(body)), context(operation));
    expect(response.status).toBe(status); expect(await response.json()).toEqual({ status: code });
    const posts = fetch.mock.calls.filter(([, options]) => (options as RequestInit)?.method === "POST");
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0] as unknown as [string, RequestInit];
    expect(url).toBe(`https://backend.example.invalid/api/auth/privacy-erasure/${operation}`);
    expect(JSON.parse(init.body as string)).toEqual(body);
    const headers = new Headers(init.headers);
    expect(headers.has("authorization")).toBe(false); expect(headers.has("cookie")).toBe(false);
    expect(headers.get("x-hirewiz-candidate-ingress")).toBeTruthy();
    expect(response.headers.get("cache-control")).toContain("no-store"); expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.has("set-cookie")).toBe(false);
  });
  it.each([{ origin: "https://attacker.invalid" }, { "x-hirewiz-account-csrf": "bad" }, { cookie: "" }, { "content-type": "text/plain" }, { "content-encoding": "gzip" }] as Record<string, string>[])("refuses unbound requests before network", async (headers) => {
    const fetch = backend({}, 200);
    expect((await route.POST(request("request", '{"email":"synthetic@example.invalid"}', headers), context("request"))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    `{"token":"${token}","confirm_erasure":false}`, `{"token":"${token}","confirm_erasure":"true"}`,
    `{"token":"${token}","confirm_erasure":1}`, `{"token":"${token}"}`,
    `{"token":"${token}","confirm_erasure":true,"confirm_erasure":true}`,
    `{"token":"${token}","confirm_era\\u0073ure":true,"confirm_erasure":true}`,
    `{"token":"${token}","confirm_erasure":true,"other":"x"}`,
    `{"token":"${token}","confirm_erasure":true}{}`, `{"token":"short","confirm_erasure":true}`,
  ])("rejects missing/ambiguous/nonliteral confirmation without forwarding", async (body) => {
    const fetch = backend({}, 200);
    expect((await route.POST(request("confirm", body), context("confirm"))).status).toBe(422);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("exposes no GET, rejects query proof and unsupported operations, and requires signing custody", async () => {
    const fetch = backend({}, 200);
    expect("GET" in route).toBe(false);
    expect((await route.POST(request("confirm", "{}", {}, "?token=private"), context("confirm"))).status).toBe(403);
    expect((await route.POST(request("other", "{}"), context("other"))).status).toBe(404);
    vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", ""); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "");
    expect((await route.POST(request("request", '{"email":"synthetic@example.invalid"}'), context("request"))).status).toBe(503);
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([[400, { status: "ERASURE_LINK_INVALID" }, 400, "ERASURE_LINK_INVALID"],
    [422, { detail: token }, 422, "ERASURE_REQUEST_REJECTED"], [429, { private: token }, 429, "RATE_LIMITED"],
    [503, { private: token }, 503, "ERASURE_UNAVAILABLE"], [200, { status: "ACCOUNT_ERASED", token }, 503, "ERASURE_UNAVAILABLE"],
    [200, { status: "ERASURE_LINK_REQUESTED" }, 503, "ERASURE_UNAVAILABLE"]] as const)("projects finite %s response without leaking proof or replaying", async (status, data, expected, code) => {
    const fetch = backend(data, status);
    const response = await route.POST(request("confirm", JSON.stringify({ token, confirm_erasure: true })), context("confirm"));
    expect(response.status).toBe(expected); expect(await response.json()).toEqual({ status: code });
    expect(fetch.mock.calls.filter(([, options]) => (options as RequestInit)?.method === "POST")).toHaveLength(1);
  });
});
