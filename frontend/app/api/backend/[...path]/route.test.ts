// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getToken } = vi.hoisted(() => ({ getToken: vi.fn() }));
vi.mock("next-auth/jwt", () => ({ getToken }));

import { DELETE, GET, PATCH, POST, PUT } from "./route";

const site = "https://www.hirewizhq.com";
const fetchBackend = vi.fn();
const rejectedHeaders: Record<string, string>[] = [
  { "sec-fetch-site": "cross-site" },
  { origin: site, "sec-fetch-site": "cross-site" },
  { origin: "null" },
  { origin: "https://www.hirewizhq.com:8443" },
  { origin: `${site}/not-an-origin` },
  { origin: "invalid origin" },
  { referer: "https://attacker.example/form" },
];
const serverHeaders: Record<string, string>[] = [{}, { "sec-fetch-site": "none" }, { "sec-fetch-site": "same-origin" }, { referer: `${site}/billing` }];
const context = (path = ["v1", "employer-jobs", "applications", "app_fixture", "approve"]) => ({ params: Promise.resolve({ path }) });
function request(method: string, headers: Record<string, string> = {}) {
  return new NextRequest(`${site}/api/backend/v1/employer-jobs/applications/app_fixture/approve`, {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: method === "GET" ? undefined : JSON.stringify({ package_digest: "fixture-digest" }),
  });
}

beforeEach(() => {
  vi.stubEnv("BACKEND_URL", "https://backend.example.test");
  vi.stubGlobal("fetch", fetchBackend);
  getToken.mockResolvedValue({ accessToken: "fixture-backend-token" });
  fetchBackend.mockImplementation(async () => new Response(JSON.stringify({ forwarded: true }), { status: 200, headers: { "content-type": "application/json" } }));
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("BFF mutation request origin", () => {
  it("forwards a same-origin browser application approval with its authenticated bearer", async () => {
    const response = await POST(request("POST", { origin: site, "sec-fetch-site": "same-origin" }), context());
    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    const [target, options] = fetchBackend.mock.calls[0];
    expect(String(target)).toBe("https://backend.example.test/api/v1/employer-jobs/applications/app_fixture/approve");
    expect(options.headers.get("Authorization")).toBe("Bearer fixture-backend-token");
    expect(new TextDecoder().decode(options.body)).toContain("fixture-digest");
  });

  it("uses the incoming Host when Next's route URL has an internal fetch hostname", async () => {
    const incoming = new NextRequest("https://localhost:3000/api/backend/billing/orders", { method: "POST", headers: { host: "www.hirewizhq.com", origin: site, "sec-fetch-site": "same-origin", "content-type": "application/json" }, body: "{}" });
    expect((await POST(incoming, context(["billing", "orders"]))).status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    fetchBackend.mockClear();
    const foreign = new NextRequest(incoming.url, { method: "POST", headers: { host: "www.hirewizhq.com", origin: "https://localhost:3000", "x-forwarded-host": "localhost:3000" }, body: "{}" });
    expect((await POST(foreign, context(["billing", "orders"]))).status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it.each([["POST", POST], ["PUT", PUT], ["PATCH", PATCH], ["DELETE", DELETE]] as const)("rejects a foreign-origin %s before authentication or any backend mutation", async (method, handler) => {
    const response = await handler(request(method, { origin: "https://attacker.example", "sec-fetch-site": "same-site" }), context(["billing", "create-order"]));
    expect(response.status).toBe(403);
    expect(getToken).not.toHaveBeenCalled();
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it.each(rejectedHeaders)("rejects cross-site, opaque, malformed, or different-port request headers %j", async (headers) => {
    const response = await POST(request("POST", headers), context());
    expect(response.status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
    expect(getToken).not.toHaveBeenCalled();
  });

  it.each(serverHeaders)("preserves origin-less non-cross-site server requests %j", async (headers) => {
    const response = await POST(request("POST", headers), context());
    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });

  it("preserves public browser registration without a session while rejecting cross-site registration", async () => {
    getToken.mockResolvedValue(null);
    const registration = context(["auth", "register"]);
    expect((await POST(request("POST", { origin: site, "sec-fetch-site": "same-origin" }), registration)).status).toBe(200);
    expect(fetchBackend.mock.calls[0][1].headers.has("Authorization")).toBe(false);
    fetchBackend.mockClear();
    expect((await POST(request("POST", { origin: "https://attacker.example" }), registration)).status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("does not change authenticated read requests", async () => {
    const response = await GET(request("GET", { origin: "https://other.example", "sec-fetch-site": "cross-site" }), context(["auth", "profile"]));
    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
});
