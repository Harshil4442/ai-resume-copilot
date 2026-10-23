// @vitest-environment node
import { createHash } from "node:crypto";
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as route from "./route";

const origin = "https://website.example.invalid", secret = "synthetic-nextauth-private-secret";
const csrf = "c".repeat(64), token = "T".repeat(43), password = "synthetic-password-123";
let scope = 0;
beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubEnv("NEXTAUTH_URL", origin); vi.stubEnv("NEXTAUTH_SECRET", secret);
  vi.stubEnv("BACKEND_URL", "https://backend.example.invalid");
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", "6e".repeat(32));
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", `reset_fixture_${++scope}`);
});
function request(operation: string, body = JSON.stringify({ email: "owner@example.invalid" }), extra: Record<string, string> = {}, query = "") {
  return new NextRequest(`${origin}/api/password-reset/${operation}${query}`, { method: "POST", body,
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

describe("public password-reset BFF private transport", () => {
  it.each(["request", "confirm"])("forwards %s once with independent ingress proof and no bearer or browser cookie", async (operation) => {
    const body = operation === "confirm" ? { token, new_password: password } : { email: "owner@example.invalid" };
    const status = operation === "confirm" ? 200 : 202;
    const result = operation === "confirm" ? "PASSWORD_RESET" : "RESET_LINK_REQUESTED";
    const fetch = backend({ status: result }, status);
    const response = await route.POST(request(operation, JSON.stringify(body)), context(operation));
    expect(response.status).toBe(status); expect(await response.json()).toEqual({ status: result });
    const posts = fetch.mock.calls.filter(([, options]) => (options as RequestInit)?.method === "POST");
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0] as unknown as [string, RequestInit];
    expect(url).toBe(`https://backend.example.invalid/api/auth/password-reset/${operation}`);
    expect(JSON.parse(init.body as string)).toEqual(body);
    const headers = new Headers(init.headers);
    expect(headers.has("authorization")).toBe(false); expect(headers.has("cookie")).toBe(false);
    expect(headers.get("x-hirewiz-candidate-ingress")).toBeTruthy();
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.has("set-cookie")).toBe(false);
  });
  it.each([{ origin: "https://attacker.invalid" }, { "x-hirewiz-account-csrf": "bad" }, { cookie: "" }, { "content-type": "text/plain" }, { "content-encoding": "gzip" }] as Record<string, string>[])("refuses foreign/unbound requests before any network call", async (headers) => {
    const fetch = backend({}, 200);
    const response = await route.POST(request("request", undefined, headers), context("request"));
    expect(response.status).toBe(403); expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects query and unsupported operations without consuming a reset or calling backend", async () => {
    const fetch = backend({}, 200);
    expect((await route.POST(request("confirm", "{}", {}, "?token=private"), context("confirm"))).status).toBe(403);
    expect((await route.POST(request("unknown"), context("unknown"))).status).toBe(404);
    expect("GET" in route).toBe(false); expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    '{"email":"owner@example.invalid","email":"other@example.invalid"}',
    '{"email":"owner@example.invalid","em\\u0061il":"other@example.invalid"}',
    '{"email":{"nested":"owner@example.invalid"}}', '{"email":"owner@example.invalid","extra":"x"}',
    '{"email":"owner@example.invalid",}', '{"email":"owner@example.invalid"}{}',
  ])("rejects ambiguous/noncanonical body before forwarding", async (body) => {
    const fetch = backend({}, 200);
    expect((await route.POST(request("request", body), context("request"))).status).toBe(422);
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each(["short", "T".repeat(44), "!".repeat(43)])("rejects invalid reset token shape locally", async (invalid) => {
    const fetch = backend({}, 200);
    const response = await route.POST(request("confirm", JSON.stringify({ token: invalid, new_password: password })), context("confirm"));
    expect(response.status).toBe(422); expect(fetch).not.toHaveBeenCalled();
  });
  it("enforces byte and password character bounds without echoing input", async () => {
    const fetch = backend({}, 200);
    for (const body of ["x".repeat(8193), JSON.stringify({ token, new_password: "x".repeat(129) }), JSON.stringify({ token, new_password: "🔐".repeat(129) })]) {
      const response = await route.POST(request("confirm", body), context("confirm"));
      expect([422, 503]).toContain(response.status);
      expect(await response.text()).not.toContain(token);
    }
    expect(fetch).not.toHaveBeenCalled();
    backend({ status: "PASSWORD_RESET" }, 200);
    const response = await route.POST(request("confirm", JSON.stringify({ token, new_password: "🔐".repeat(128) })), context("confirm"));
    expect(response.status).toBe(200);
  });
  it.each([[400, { status: "RESET_LINK_INVALID" }, 400, "RESET_LINK_INVALID"],
    [422, { detail: [{ input: password }] }, 422, "RESET_REQUEST_REJECTED"],
    [429, { private: token }, 429, "RATE_LIMITED"],
    [503, { detail: token }, 503, "RESET_UNAVAILABLE"],
    [200, { status: "PASSWORD_RESET", token }, 503, "RESET_UNAVAILABLE"],
    [200, { status: "RESET_LINK_INVALID" }, 503, "RESET_UNAVAILABLE"]] as const)("projects finite %s result and never retries confirm", async (status, data, expected, code) => {
    const fetch = backend(data, status);
    const response = await route.POST(request("confirm", JSON.stringify({ token, new_password: password })), context("confirm"));
    expect(response.status).toBe(expected); expect(await response.json()).toEqual({ status: code });
    expect(fetch.mock.calls.filter(([, options]) => (options as RequestInit)?.method === "POST")).toHaveLength(1);
  });
  it("fails closed without reset ingress custody while ordinary legacy auth remains unchanged", async () => {
    const fetch = backend({}, 200);
    vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", ""); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "");
    const response = await route.POST(request("request"), context("request"));
    expect(response.status).toBe(503); expect(fetch).not.toHaveBeenCalled();
  });
});
