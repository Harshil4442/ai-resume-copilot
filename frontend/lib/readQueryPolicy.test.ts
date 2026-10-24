import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { MAX_PROGRESS_READS, progressPollDelay, retryRead } from "./readQueryPolicy";

describe("bounded owner reads", () => {
  it.each([429, 503])("does not automatically retry HTTP%s", async (status) => {
    const read = vi.fn().mockRejectedValue(Object.assign(new Error("Read unavailable"), { status }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: retryRead, retryDelay: 0 } } });
    await expect(client.fetchQuery({ queryKey: ["status"], queryFn: read })).rejects.toMatchObject({ status });
    expect(read).toHaveBeenCalledTimes(1);
    client.clear();
  });

  it("retains the single existing retry for other read failures", async () => {
    const read = vi.fn().mockRejectedValue(new Error("Read failed"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: retryRead, retryDelay: 0 } } });
    await expect(client.fetchQuery({ queryKey: ["status"], queryFn: read })).rejects.toThrow("Read failed");
    expect(read).toHaveBeenCalledTimes(2);
    client.clear();
  });

  it("backs off progress reads and stops at a finite limit", () => {
    expect(Array.from({ length: MAX_PROGRESS_READS + 1 }, (_, count) => progressPollDelay(count)))
      .toEqual([5000, 5000, 10000, 20000, 30000, 30000, 30000, 30000, false]);
  });
});
