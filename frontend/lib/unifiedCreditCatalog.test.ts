// @vitest-environment node
const eligibleServices = [{ operation: "resume_tailor", credits: 8, deliverable: "Tailored resume with preserved source layout.", supported_limits: { max_pages: 2 }, source_formats: ["pdf", "docx"], route_version: "reviewed-v1" }];
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { getPublicUnifiedCreditCatalog } from "./unifiedCreditCatalog";
const catalog = { schema_version: "unified-credits-v1", eligible_services: eligibleServices, catalog_version: "v1", market: "IN", currency: "INR", server_time: "2026-10-27T00:00:00Z", checkout_enabled: false, products: [] };
const request = vi.fn<typeof fetch>();
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal("fetch", request); vi.stubEnv("BACKEND_URL", "https://backend.example.test/api/"); request.mockReset(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
describe("unified public pricing transport", () => {
  it("uses one fresh server catalog read and never substitutes legacy products", async () => {
    request.mockResolvedValue(Response.json(catalog));
    expect(await getPublicUnifiedCreditCatalog()).toEqual(catalog);
    expect(request).toHaveBeenCalledExactlyOnceWith("https://backend.example.test/api/public/credits/catalog", expect.objectContaining({ cache: "no-store", redirect: "error" }));
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(["http", "json", "schema", "network"])("returns unavailable after %s failure", async (failure) => {
    if (failure === "http") request.mockResolvedValue(new Response("Off", { status: 404 }));
    if (failure === "json") request.mockResolvedValue(new Response("not json"));
    if (failure === "schema") request.mockResolvedValue(Response.json({ ...catalog, schema_version: "legacy" }));
    if (failure === "network") request.mockRejectedValue(new Error("Offline"));
    expect(await getPublicUnifiedCreditCatalog()).toBeNull(); expect(request).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("has an overall 2.5-second deadline including a stalled response body", async () => {
    let signal: AbortSignal | null | undefined;
    request.mockImplementation((_url, options) => { signal = options?.signal; return Promise.resolve({ ok: true, json: () => new Promise(() => {}) } as Response); });
    const pending = getPublicUnifiedCreditCatalog(); await vi.advanceTimersByTimeAsync(2_500);
    expect(await pending).toBeNull(); expect(signal?.aborted).toBe(true); expect(request).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("does not request an unconfigured backend", async () => { vi.stubEnv("BACKEND_URL", ""); expect(await getPublicUnifiedCreditCatalog()).toBeNull(); expect(request).not.toHaveBeenCalled(); });
});
