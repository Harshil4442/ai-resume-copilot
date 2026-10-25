// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { maxDuration, POST } from "./route";

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
