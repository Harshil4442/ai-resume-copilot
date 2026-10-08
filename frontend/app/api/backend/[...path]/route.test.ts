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

describe("catalog-only latency propagation", () => {
  const catalogContext = () => context(["v1", "employer-jobs", "catalog"]);

  it("removes client correlation input and safely propagates generated backend timing and ID", async () => {
    fetchBackend.mockResolvedValueOnce(new Response('{"balance":13}', { headers: {
      "content-type": "application/json", "x-correlation-id": "a".repeat(32),
      "server-timing": "db_acquire;dur=4.2, auth_lookup;dur=2.1, catalog_sources;dur=5, catalog_render;dur=1;desc=PRIVATE_SQL_TOKEN_COOKIE_URL",
    } }));
    const response = await GET(request("GET", { "x-correlation-id": "PRIVATE_USER_ID" }), catalogContext());
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    expect(fetchBackend.mock.calls[0][1].headers.has("x-correlation-id")).toBe(false);
    expect(fetchBackend.mock.calls[0][1].cache).toBe("no-store");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ balance: 13 });
    expect(response.headers.get("cache-control")).toBe("private, no-store, max-age=0");
    expect(response.headers.get("pragma")).toBe("no-cache");
    expect(response.headers.get("x-correlation-id")).toBe("a".repeat(32));
    const value = response.headers.get("server-timing");
    expect(value).toContain("db_acquire;dur=4.2");
    expect(value).toMatch(/bff_session;dur=\d+\.\d, bff_backend_headers;dur=\d+\.\d$/);
    expect(value).not.toContain("PRIVATE");
    expect(value).not.toContain("catalog_render");
  });

  it("returns headers and passes the original stream before its final chunk exists", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({ start(value) {
      controller = value;
      value.enqueue(new TextEncoder().encode("first"));
    } });
    fetchBackend.mockResolvedValueOnce(new Response(stream, { status: 200 }));
    const response = await GET(request("GET"), catalogContext());
    expect(response.body).toBe(stream);
    const reader = response.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("first");
    controller.enqueue(new TextEncoder().encode("last"));
    controller.close();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("last");
    expect((await reader.read()).done).toBe(true);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });

  it("does not propagate backend timing on any other route or method", async () => {
    const backend = () => new Response("{}", { headers: { "server-timing": "PRIVATE_SQL;dur=1", "x-correlation-id": "existing-correlation" } });
    fetchBackend.mockImplementation(backend);
    const response = await GET(request("GET", { "x-correlation-id": "existing-client-id" }), context(["auth", "profile"]));
    expect(response.headers.has("server-timing")).toBe(false);
    expect(fetchBackend.mock.calls[0][1].headers.get("x-correlation-id")).toBe("existing-client-id");
    expect(response.headers.get("x-correlation-id")).toBe("existing-correlation");
    const mutation = await POST(request("POST", { origin: site }), catalogContext());
    expect(mutation.headers.has("server-timing")).toBe(false);
  });

  it.each([[new Error("PRIVATE_BACKEND_URL_TOKEN"), 502, "The backend is unavailable"], [new DOMException("PRIVATE_EXCEPTION", "TimeoutError"), 504, "The backend request timed out"]] as const)("preserves backend failure/timeout semantics", async (error, status, detail) => {
    fetchBackend.mockRejectedValueOnce(error);
    const response = await GET(request("GET"), catalogContext());
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ detail });
    expect(response.headers.get("server-timing")).toMatch(/^bff_session;dur=\d+\.\d$/);
    expect(response.headers.get("server-timing")).not.toContain("bff_backend_headers");
    expect(JSON.stringify(Array.from(response.headers))).not.toContain("PRIVATE");
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });

  it("does not request the backend when there is no authenticated BFF session", async () => {
    getToken.mockResolvedValueOnce(null);
    const response = await GET(request("GET"), catalogContext());
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated" });
    expect(fetchBackend).not.toHaveBeenCalled();
  });
});
