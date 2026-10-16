// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";

const origin = `chrome-extension://${"a".repeat(32)}`;
const fetchBackend = vi.fn();
const context = { params: Promise.resolve({ operation: "status" }) };
const rejected: Record<string, string>[] = [{ origin: "https://www.hirewizhq.com" }, { origin: "" }, { cookie: "candidate-session=synthetic" }, { authorization: "Bearer synthetic" }];
function request(headers: Record<string, string> = {}) {
  return new NextRequest("https://www.hirewizhq.com/api/browser-pairing/device/status", { method: "POST",
    headers: { origin, "content-type": "application/json", ...headers }, body: '{"signed":"synthetic-proof"}' });
}
beforeEach(() => {
  vi.stubEnv("BROWSER_PAIRING_GATEWAY_SECRET", "a1".repeat(32));
  vi.stubEnv("BROWSER_PAIRING_WEBSITE_ORIGIN", "https://www.hirewizhq.com");
  vi.stubEnv("BACKEND_URL", "https://native-backend.example.test");
  vi.stubGlobal("fetch", fetchBackend); fetchBackend.mockResolvedValue(Response.json({ status: "REQUESTED", pairing_id: "00000000-0000-4000-8000-000000000001", device_id: "00000000-0000-4000-8000-000000000002" }));
});
afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
describe("device BFF contains no candidate web credentials", () => {
  it("forwards only exact extension Origin and bounded signed JSON with no cookie, bearer or candidate assertion", async () => {
    expect((await POST(request(), context)).status).toBe(200);
    const [target, options] = fetchBackend.mock.calls[0];
    expect(target).toBe("https://native-backend.example.test/api/v1/browser-pairing/device/status");
    expect(options.headers).toEqual({ "Content-Type": "application/json", Origin: origin });
    expect(options.credentials).toBe("omit"); expect(options.redirect).toBe("error");
    expect(new TextDecoder().decode(options.body)).toBe('{"signed":"synthetic-proof"}');
  });
  it.each(rejected)("rejects a browser or general web authentication channel", async (headers) => {
    expect((await POST(request(headers), context)).status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("withholds malformed backend outputs and unexpected private fields", async () => {
    fetchBackend.mockResolvedValueOnce(Response.json({ status: "REQUESTED", pairing_id: "00000000-0000-4000-8000-000000000001", device_id: "00000000-0000-4000-8000-000000000002", password: "synthetic-private-upstream" }));
    const response = await POST(request(), context);
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("synthetic-private-upstream");
  });
  it.each([[403, "never"], [503, "never"], [403, "reject"], [503, "reject"]] as const)("preserves upstream %s when cancellation will %s", async (status, cancellation) => {
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
      POST(request(), context),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("Pairing error response did not settle")), 250); }),
    ]).finally(() => clearTimeout(timer));
    expect(cancelled).toBe(true);
    expect(body.locked).toBe(false);
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ detail: "Current browser pairing is unavailable" });
    expect(response.headers.get("cache-control")).toContain("no-store");
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
});
