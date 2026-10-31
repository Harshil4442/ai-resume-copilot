// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, maxDuration, POST } from "./route";

const origin = `chrome-extension://${"a".repeat(32)}`;
const fetchBackend = vi.fn();
const context = { params: Promise.resolve({ operation: "status" }) };
const rejected: Record<string, string>[] = [{ origin: "https://www.hirewizhq.com" }, { origin: "" }, { cookie: "candidate-session=synthetic" }, { authorization: "Bearer synthetic" }];

function fakeTransportClock() {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("synthetic-private-timeout", "TimeoutError")), milliseconds);
    return controller.signal;
  });
}

function delayedBackend(milliseconds: number | null) {
  return (_input: unknown, options: RequestInit) => new Promise<Response>((resolve, reject) => {
    const signal = options.signal;
    if (!signal) throw new Error("Expected a bounded backend request");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    if (milliseconds !== null) timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve(Response.json({ status: "REQUESTED", pairing_id: "00000000-0000-4000-8000-000000000001", device_id: "00000000-0000-4000-8000-000000000002" }));
    }, milliseconds);
  });
}
function request(headers: Record<string, string> = {}, operation = "status") {
  return new NextRequest(`https://www.hirewizhq.com/api/browser-pairing/device/${operation}`, { method: "POST",
    headers: { origin, "content-type": "application/json", ...headers }, body: '{"signed":"synthetic-proof"}' });
}
beforeEach(() => {
  vi.stubEnv("BROWSER_PAIRING_GATEWAY_SECRET", "a1".repeat(32));
  vi.stubEnv("BROWSER_PAIRING_WEBSITE_ORIGIN", "https://www.hirewizhq.com");
  vi.stubEnv("BACKEND_URL", "https://native-backend.example.test");
  vi.stubGlobal("fetch", fetchBackend); fetchBackend.mockResolvedValue(Response.json({ status: "REQUESTED", pairing_id: "00000000-0000-4000-8000-000000000001", device_id: "00000000-0000-4000-8000-000000000002" }));
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
describe("public same-route readiness carries no pairing authority", () => {
  const backend = "https://ai-resume-parser-mqff62d75a-uc.a.run.app";
  const extension = "chrome-extension://pnjmegppacleipajnifjmbcopjjffklb";
  function readiness(headers: Record<string, string> = {}, suffix = "status", signal?: AbortSignal) {
    return new NextRequest(`https://www.hirewizhq.com/api/browser-pairing/device/${suffix}`, { headers, signal });
  }
  beforeEach(() => { vi.stubEnv("BACKEND_URL", backend); fetchBackend.mockResolvedValue(new Response("private-health-body", { status: 200, headers: { "Set-Cookie": "private-upstream-cookie" } })); });
  it.each([{}, { origin: extension }] as Record<string, string>[])("allows absent or exact filtered Origin and returns only bodyless204", async (headers) => {
    const response = await GET(readiness(headers), context);
    expect(response.status).toBe(204); expect(await response.text()).toBe("");
    expect(response.headers.get("set-cookie")).toBeNull(); expect(response.headers.get("content-type")).toBeNull();
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    expect(fetchBackend.mock.calls[0][0]).toBe(`${backend}/api/health`);
    expect(fetchBackend.mock.calls[0][1]).toEqual({ method: "GET", credentials: "omit", redirect: "error", cache: "no-store", signal: expect.any(AbortSignal) });
  });
  it.each(["prepare", "inspect-key", "status", "challenge", "refresh-challenge"])("warms only exact signed operation %s", async (operation) => {
    expect((await GET(readiness({}, operation), { params: Promise.resolve({ operation }) })).status).toBe(204);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
  it.each(["create", "complete", "refresh", "../status", "health", "STATUS"])("rejects other operation %s without upstream activity", async (operation) => {
    expect((await GET(readiness(), { params: Promise.resolve({ operation }) })).status).toBe(403);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
  it.each([{ origin: "null" }, { origin: "https://www.hirewizhq.com" }, { origin }, { cookie: "" }, { authorization: "" },
    { "content-type": "application/json" }, { "content-length": "0" }, { "content-encoding": "gzip" }, { "transfer-encoding": "chunked" }] as Record<string, string>[])("rejects credentials/content or wrong supplied Origin", async (headers) => {
    expect((await GET(readiness(headers), context)).status).toBe(403); expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("rejects query, wrong website origin, synthetic body and automatic HEAD without forwarding", async () => {
    expect((await GET(readiness({}, "status?target=https://127.0.0.1"), context)).status).toBe(403);
    expect((await GET(new NextRequest("https://unreviewed.example/api/browser-pairing/device/status"), context)).status).toBe(503);
    expect((await GET(new NextRequest("https://www.hirewizhq.com/api/browser-pairing/device/status", { method: "HEAD" }), context)).status).toBe(403);
    const body = readiness(); Object.defineProperty(body, "body", { value: new ReadableStream() });
    expect((await GET(body, context)).status).toBe(403); expect(fetchBackend).not.toHaveBeenCalled();
  });
  it.each(["http://127.0.0.1", "https://127.0.0.1", "https://private.example", `${backend}/other`, `${backend}?target=other`,
    "https://user:secret@ai-resume-parser-mqff62d75a-uc.a.run.app", `${backend}:8443`])("refuses backend drift/SSRF target %s", async (target) => {
    vi.stubEnv("BACKEND_URL", target);
    expect((await GET(readiness(), context)).status).toBe(503); expect(fetchBackend).not.toHaveBeenCalled();
  });
  it("accepts the observed regional service alias only at exact health and never exposes configuration failures", async () => {
    vi.stubEnv("BACKEND_URL", "https://ai-resume-parser-157225724590.us-central1.run.app/api");
    expect((await GET(readiness(), context)).status).toBe(204);
    expect(fetchBackend.mock.calls[0][0]).toBe("https://ai-resume-parser-157225724590.us-central1.run.app/api/health");
    fetchBackend.mockClear(); vi.stubEnv("BROWSER_PAIRING_GATEWAY_SECRET", "private-invalid-config");
    const failed = await GET(readiness(), context);
    expect(failed.status).toBe(503); expect(await failed.text()).toBe(""); expect(fetchBackend).not.toHaveBeenCalled();
  });
  it.each([204, 302, 403, 500])("fails closed on health status %s without parsing/reflection/retry", async (status) => {
    fetchBackend.mockResolvedValueOnce(new Response(null, { status }));
    const response = await GET(readiness(), context);
    expect(response.status).toBe(503); expect(await response.text()).toBe(""); expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
  it("observes ten seconds of health latency inside a separate fifteen-second bound", async () => {
    fakeTransportClock(); fetchBackend.mockImplementationOnce(delayedBackend(10000));
    const pending = GET(readiness(), context); await vi.advanceTimersByTimeAsync(9999);
    expect(fetchBackend.mock.calls[0][1].signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); expect((await pending).status).toBe(204);
    expect(maxDuration).toBe(45); expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
  it("aborts at fifteen seconds and never retries or returns late availability", async () => {
    fakeTransportClock(); fetchBackend.mockImplementationOnce(delayedBackend(null));
    const pending = GET(readiness(), context); await vi.advanceTimersByTimeAsync(14999);
    expect(fetchBackend.mock.calls[0][1].signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); const response = await pending;
    expect(response.status).toBe(503); expect(await response.text()).toBe("");
    await vi.advanceTimersByTimeAsync(60000); expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
  it("propagates request cancellation and refuses pre-aborted requests", async () => {
    fetchBackend.mockImplementationOnce(delayedBackend(null)); const controller = new AbortController();
    const pending = GET(readiness({}, "status", controller.signal), context); await Promise.resolve(); await Promise.resolve();
    controller.abort(); expect((await pending).status).toBe(503); expect(fetchBackend).toHaveBeenCalledTimes(1);
    fetchBackend.mockClear(); expect((await GET(readiness({}, "status", controller.signal), context)).status).toBe(503);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
});
describe("device BFF contains no candidate web credentials", () => {
  it("returns a valid 13.025-second device response through the bounded route", async () => {
    fakeTransportClock();
    fetchBackend.mockImplementationOnce(delayedBackend(13_025));
    const pending = POST(request(), context);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(13_024);
    expect(fetchBackend.mock.calls[0][1].signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const response = await pending;
    expect(response.status).toBe(200);
    expect((await response.json()).status).toBe("REQUESTED");
    expect(maxDuration).toBe(45);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
  it("aborts an unsettled device request at 35 seconds and requires explicit reconciliation", async () => {
    fakeTransportClock();
    fetchBackend.mockImplementationOnce(delayedBackend(null));
    let settled = false;
    const pending = POST(request(), context).then((response) => { settled = true; return response; });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(34_999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const response = await pending;
    expect(fetchBackend.mock.calls[0][1].signal.aborted).toBe(true);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "Browser pairing is currently unavailable" });
    expect(response.headers.get("cache-control")).toContain("no-store");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
  });
  it("forwards inspect-key once through the credential-free device route", async () => {
    const reply = { protocol_version: 2, key_sha256: "a".repeat(64), extension_id: "a".repeat(32),
      executor_revision: "companion_test", status: "UNCLAIMED" };
    fetchBackend.mockResolvedValueOnce(Response.json(reply));
    const response = await POST(request({}, "inspect-key"), { params: Promise.resolve({ operation: "inspect-key" }) });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(reply);
    expect(fetchBackend).toHaveBeenCalledTimes(1);
    expect(fetchBackend.mock.calls[0][0]).toBe("https://native-backend.example.test/api/v1/browser-pairing/device/inspect-key");
    expect(fetchBackend.mock.calls[0][1].headers).toEqual({ "Content-Type": "application/json", Origin: origin });
    expect(fetchBackend.mock.calls[0][1].credentials).toBe("omit");
  });
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
