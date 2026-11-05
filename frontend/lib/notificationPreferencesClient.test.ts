import { afterEach, describe, expect, it, vi } from "vitest";
import { changeNotificationPreference, readNotificationPreference, unsubscribeNotifications } from "./notificationPreferencesClient";

const off = { weekly_digest: false, generation: 7, policy_version: "weekly-v1", next_digest_at: null, projection_current: true };
afterEach(() => vi.unstubAllGlobals());

describe("bounded non-retrying notification transport", () => {
  it("uses the owner GET and explicit expected-generation PUT without retries", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json(off)));
    vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;
    await readNotificationPreference(signal);
    await changeNotificationPreference(off, true, signal);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][0]).toBe("/api/backend/v1/notifications/preferences");
    const options = fetcher.mock.calls[1][1];
    expect(options.method).toBe("PUT"); expect(options.cache).toBe("no-store");
    expect(JSON.parse(options.body)).toEqual({ expected_generation: 7, weekly_digest: true, explicit_confirmation: true });
  });
  it("sends public proof only in a POST body with a no-referrer policy", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(off)); vi.stubGlobal("fetch", fetcher);
    await unsubscribeNotifications("synthetic-proof", new AbortController().signal);
    expect(fetcher).toHaveBeenCalledOnce();
    const [path, options] = fetcher.mock.calls[0];
    expect(path).toBe("/api/backend/public/notifications/unsubscribe");
    expect(options.method).toBe("POST"); expect(options.referrerPolicy).toBe("no-referrer");
    expect(JSON.parse(options.body)).toEqual({ token: "synthetic-proof" });
  });
  it("does not parse or reflect a rejected proof response and makes no retry", async () => {
    const response = Response.json({ detail: "PRIVATE-PROOF" }, { status: 503 });
    const json = vi.spyOn(response, "json");
    const fetcher = vi.fn().mockResolvedValue(response); vi.stubGlobal("fetch", fetcher);
    await expect(unsubscribeNotifications("synthetic-proof", new AbortController().signal)).rejects.toThrow("could not be confirmed");
    expect(json).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledOnce();
  });
  it.each([{ ...off, generation: -1 }, { ...off, weekly_digest: "false" }, { ...off, projection_current: null }, { ...off, policy_version: "<unsafe>" }])("rejects malformed server preference metadata", async (row) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(row)));
    await expect(readNotificationPreference(new AbortController().signal)).rejects.toThrow("could not be confirmed");
  });
  it("rejects a success-shaped public response that would enable email", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...off, weekly_digest: true })));
    await expect(unsubscribeNotifications("synthetic-proof", new AbortController().signal)).rejects.toThrow();
  });
});
