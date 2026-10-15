// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { getPublicBillingCatalog } from "./billingCatalog";

const catalog = {
  catalog_version: "fixture-current",
  market: "IN",
  checkout_enabled: false,
  provider: null,
  products: [],
};
const fetchCatalog = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv("BACKEND_URL", "https://backend.example.test/api/");
  vi.stubGlobal("fetch", fetchCatalog);
});

afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("public billing catalog request deadline", () => {
  it("keeps each successful price/checkout read fresh and clears the deadline", async () => {
    fetchCatalog.mockResolvedValueOnce(Response.json(catalog));
    fetchCatalog.mockResolvedValueOnce(Response.json({ ...catalog, catalog_version: "fixture-newer", checkout_enabled: true }));

    expect(await getPublicBillingCatalog()).toEqual(catalog);
    expect(await getPublicBillingCatalog()).toMatchObject({ catalog_version: "fixture-newer", checkout_enabled: true });
    expect(fetchCatalog).toHaveBeenCalledTimes(2);
    for (const [url, options] of fetchCatalog.mock.calls) {
      expect(url).toBe("https://backend.example.test/api/public/billing/catalog");
      expect(options).toMatchObject({ cache: "no-store", headers: { Accept: "application/json" } });
      expect(options?.signal?.aborted).toBe(false);
    }
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts a stalled upstream request at 2.5 seconds and never retries", async () => {
    let signal: AbortSignal | null | undefined;
    fetchCatalog.mockImplementation((_url, options) => {
      signal = options?.signal;
      return new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true }));
    });
    let settled = false;
    const pending = getPublicBillingCatalog().then((result) => { settled = true; return result; });
    await vi.advanceTimersByTimeAsync(2_499);
    expect(settled).toBe(false);
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await pending).toBeNull();
    expect(signal?.aborted).toBe(true);
    expect(fetchCatalog).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("uses one overall deadline across response headers and a stalled response body", async () => {
    let signal: AbortSignal | null | undefined;
    let jsonStarted = false;
    fetchCatalog.mockImplementation((_url, options) => {
      signal = options?.signal;
      return new Promise((resolve) => setTimeout(() => resolve({
        ok: true,
        json: () => { jsonStarted = true; return new Promise(() => {}); },
      } as Response), 2_000));
    });
    let settled = false;
    const pending = getPublicBillingCatalog().then((result) => { settled = true; return result; });
    await vi.advanceTimersByTimeAsync(2_000);
    expect(jsonStarted).toBe(true);
    await vi.advanceTimersByTimeAsync(499);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await pending).toBeNull();
    expect(signal?.aborted).toBe(true);
    expect(fetchCatalog).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(["http", "json", "schema", "network"])("fails closed on %s failure without a retry or pending timer", async (failure) => {
    if (failure === "http") fetchCatalog.mockResolvedValue(new Response("Unavailable", { status: 503 }));
    if (failure === "json") fetchCatalog.mockResolvedValue(new Response("not JSON", { status: 200 }));
    if (failure === "schema") fetchCatalog.mockResolvedValue(Response.json({ checkout_enabled: true }));
    if (failure === "network") fetchCatalog.mockRejectedValue(new TypeError("Network unavailable"));
    expect(await getPublicBillingCatalog()).toBeNull();
    expect(fetchCatalog).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("returns unavailable without fetching when the backend is not configured", async () => {
    vi.stubEnv("BACKEND_URL", "");
    expect(await getPublicBillingCatalog()).toBeNull();
    expect(fetchCatalog).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
