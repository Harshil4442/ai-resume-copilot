import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useProgressQuery } from "./useProgressQuery";

type Progress = { status: "pending" | "done" };
let client: QueryClient;
let visible: DocumentVisibilityState;
const read = vi.fn<() => Promise<Progress>>();

function Probe() {
  const query = useProgressQuery({ queryKey: ["progress"], queryFn: read, active: (value) => value?.status !== "done" });
  return <><p>{query.data?.status ?? "loading"}</p><p>{query.pollingPaused ? "Checks paused" : "Automatic checks"}</p><button disabled={query.isFetching} onClick={() => void query.refetch()}>Check status</button></>;
}
async function tick(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function visibility(value: DocumentVisibilityState) {
  visible = value;
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-24T00:00:00Z"));
  visible = "visible"; vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visible);
  client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
  read.mockReset().mockResolvedValue({ status: "pending" });
});
afterEach(() => { cleanup(); client.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("backs off, pauses hidden tabs, then stops automatically while keeping explicit reads available", async () => {
  render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(1);
  await tick(5000); expect(read).toHaveBeenCalledTimes(2);
  await tick(9000); expect(read).toHaveBeenCalledTimes(2);
  await tick(1000); expect(read).toHaveBeenCalledTimes(3);
  await visibility("hidden"); await tick(120000); expect(read).toHaveBeenCalledTimes(3);
  await visibility("visible"); await tick(1); expect(read).toHaveBeenCalledTimes(3);
  await tick(20000); expect(read).toHaveBeenCalledTimes(4);
  for (let i = 0; i < 4; i++) await tick(30000);
  expect(read).toHaveBeenCalledTimes(8); expect(screen.getByText("Checks paused")).toBeInTheDocument();
  await tick(120000); expect(read).toHaveBeenCalledTimes(8);
  fireEvent.click(screen.getByRole("button", { name: "Check status" })); await tick(1);
  expect(read).toHaveBeenCalledTimes(9);
  await tick(120000); expect(read).toHaveBeenCalledTimes(9);
});

it.each([429, 503])("stops on HTTP%s, including after tab visibility changes, until an explicit status read", async (status) => {
  read.mockResolvedValueOnce({ status: "pending" }).mockRejectedValueOnce(Object.assign(new Error("Unavailable"), { status })).mockResolvedValue({ status: "done" });
  const view = render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); await tick(5000); expect(read).toHaveBeenCalledTimes(2);
  await tick(120000); await visibility("hidden"); await visibility("visible"); await tick(120000);
  expect(read).toHaveBeenCalledTimes(2); expect(screen.getByText("Checks paused")).toBeInTheDocument();
  view.unmount();
  render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Check status" })); await tick(1);
  expect(read).toHaveBeenCalledTimes(3); expect(screen.getByText("done")).toBeInTheDocument();
  await tick(120000); expect(read).toHaveBeenCalledTimes(3);
});

it("reads fresh successful cached status when the same owner reopens a review", async () => {
  read.mockResolvedValue({ status: "done" });
  const view = render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(1); expect(screen.getByText("done")).toBeInTheDocument();
  view.unmount(); read.mockResolvedValue({ status: "pending" });
  render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(2); expect(screen.getByText("pending")).toBeInTheDocument();
});
