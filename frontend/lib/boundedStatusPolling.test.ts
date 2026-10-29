import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { boundedStatusChecks, newStatusReadBudget, retryAfterMilliseconds } from "./boundedStatusPolling";
import { ApiError, apiGet } from "./api";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-30T00:00:00Z")); });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const active = () => true;
it("uses at most8 adaptive automatic reads per order budget and manual refresh reads only once", async () => {
  const start = Date.now(), times: number[] = [], budget = newStatusReadBudget();
  const read = vi.fn(async () => { times.push(Date.now() - start); return "pending"; });
  const run = boundedStatusChecks({ budget, read, active, automatic: true, signal: new AbortController().signal });
  await vi.advanceTimersByTimeAsync(155_000);
  expect(await run).toBe("paused");
  expect(times).toEqual([0, 5000, 15000, 35000, 65000, 95000, 125000, 155000]);
  expect(await boundedStatusChecks({ budget, read, active, automatic: true, signal: new AbortController().signal })).toBe("paused");
  expect(read).toHaveBeenCalledTimes(8);
  expect(await boundedStatusChecks({ budget, read, active, automatic: false, signal: new AbortController().signal })).toBe("pending");
  expect(read).toHaveBeenCalledTimes(9);
});
it("stops on a hidden tab with no background or visibility catch-up burst", async () => {
  let visible = true;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visible ? "visible" : "hidden");
  const read = vi.fn(async () => "pending");
  const run = boundedStatusChecks({ budget: newStatusReadBudget(), read, active, automatic: true, signal: new AbortController().signal });
  await vi.advanceTimersByTimeAsync(1); visible = false; document.dispatchEvent(new Event("visibilitychange"));
  expect(await run).toBe("paused");
  await vi.advanceTimersByTimeAsync(600_000); visible = true; document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(600_000); expect(read).toHaveBeenCalledOnce();
});
it("cancels a pending timer without another GET", async () => {
  const controller = new AbortController(), read = vi.fn(async () => "pending");
  const run = boundedStatusChecks({ budget: newStatusReadBudget(), read, active, automatic: true, signal: controller.signal });
  const refused = expect(run).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(1); controller.abort(); await refused;
  await vi.advanceTimersByTimeAsync(200_000); expect(read).toHaveBeenCalledOnce();
});
it.each([429, 503])("respects HTTP%s retry minimum across automatic and manual checks, without retrying it", async (status) => {
  const budget = newStatusReadBudget(), read = vi.fn().mockRejectedValue(new ApiError("Capacity", status, {}, 120_000));
  await expect(boundedStatusChecks({ budget, read, active, automatic: true, signal: new AbortController().signal })).rejects.toThrow("Capacity");
  read.mockResolvedValue("pending");
  await vi.advanceTimersByTimeAsync(119_999);
  await expect(boundedStatusChecks({ budget, read, active, automatic: false, signal: new AbortController().signal })).rejects.toThrow("retry interval");
  expect(read).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(1);
  await boundedStatusChecks({ budget, read, active, automatic: false, signal: new AbortController().signal });
  expect(read).toHaveBeenCalledTimes(2);
});
it("keeps long valid Retry-After values and bounds malformed input to a conservative fallback", () => {
  expect(retryAfterMilliseconds("9999999999", 429)).toBe(9_999_999_999_000);
  expect(retryAfterMilliseconds("9".repeat(256), 429)).toBe(Infinity);
  expect(retryAfterMilliseconds(new Date(Date.now() + 180_000).toUTCString(), 503)).toBe(180_000);
  for (const value of ["NaN", "-1", "1e9", "https://synthetic.invalid", "0"]) {
    expect(retryAfterMilliseconds(value, 429)).toBe(60_000);
    expect(retryAfterMilliseconds(value, 503)).toBe(30_000);
  }
});
it("retains the real response Retry-After in ApiError without displaying provider headers", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Capacity" }, { status: 503, headers: { "Retry-After": "180" } })));
  await expect(apiGet("/billing/orders/synthetic")).rejects.toMatchObject({ status: 503, retryAfterMs: 180_000, message: "Capacity" });
});
it("keeps an unrepresentably distant minimum paused instead of scheduling an overflowing timer", async () => {
  const budget = newStatusReadBudget();
  const read = vi.fn().mockRejectedValue(new ApiError("Capacity", 429, {}, retryAfterMilliseconds("9".repeat(256), 429)));
  await expect(boundedStatusChecks({ budget, read, active, automatic: true, signal: new AbortController().signal })).rejects.toThrow("Capacity");
  await vi.advanceTimersByTimeAsync(1_000_000);
  await expect(boundedStatusChecks({ budget, read, active, automatic: false, signal: new AbortController().signal })).rejects.toThrow("retry interval");
  expect(read).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
});
