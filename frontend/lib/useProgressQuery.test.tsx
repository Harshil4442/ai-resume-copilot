import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { apiGet } from "./api";
import { useProgressQuery } from "./useProgressQuery";

type Progress = { status: "pending" | "done" };
let client: QueryClient;
let visible: DocumentVisibilityState;
const read = vi.fn<() => Promise<Progress>>();

function Probe({ enabled = true, id = "progress", label = "Check status" }: { enabled?: boolean; id?: string; label?: string }) {
  const query = useProgressQuery({ queryKey: [id], queryFn: read, enabled, active: (value) => value?.status !== "done" });
  return <><p>{query.data?.status ?? "loading"}</p><p>{query.pollingPaused ? "Checks paused" : "Automatic checks"}</p>
    <button disabled={query.statusCheckDisabled} onClick={query.checkStatus}>{label}</button>
    <button onClick={() => void query.refetch()}>System refresh</button></>;
}
async function tick(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function visibility(value: DocumentVisibilityState) {
  visible = value;
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-24T00:00:00Z"));
  visible = "visible"; vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visible);
  client = new QueryClient();
  read.mockReset().mockResolvedValue({ status: "pending" });
});
afterEach(() => { cleanup(); client.clear(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("backs off, pauses hidden tabs, then stops automatically while keeping explicit reads available", async () => {
  render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(1);
  await tick(5000); expect(read).toHaveBeenCalledTimes(2);
  await tick(9000); expect(read).toHaveBeenCalledTimes(2);
  await tick(1000); expect(read).toHaveBeenCalledTimes(3);
  await visibility("hidden");
  expect(screen.getByRole("button", { name: "Check status" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  await tick(120000); expect(read).toHaveBeenCalledTimes(3);
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

it.each([[429, 60], [503, 120]])("honors HTTP%s Retry-After on explicit status reads across remount", async (status, seconds) => {
  const path = "/v1/analysis-runs/synthetic";
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({}, { status, headers: { "Retry-After": String(seconds) } }))
    .mockResolvedValueOnce(Response.json({ status: "done" }));
  vi.stubGlobal("fetch", fetcher);
  read.mockImplementation(() => apiGet<Progress>(path));
  const show = () => render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  const first = show();
  await tick(1);
  const button = () => screen.getByRole("button", { name: "Check status" });
  expect(button()).toBeDisabled();
  fireEvent.click(button());
  await tick(seconds * 1000 - 2);
  first.unmount();
  show();
  expect(button()).toBeDisabled();
  fireEvent.click(button());
  expect(fetcher).toHaveBeenCalledTimes(1);
  await tick(1);
  expect(button()).toBeEnabled();
  expect(fetcher).toHaveBeenCalledTimes(1);
  fireEvent.click(button());
  fireEvent.click(button());
  await tick(1);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls.map(([url, init]) => [url, init.method ?? "GET"])).toEqual([
    ["/api/backend" + path, "GET"], ["/api/backend" + path, "GET"],
  ]);
  expect(screen.getByText("done")).toBeInTheDocument();
});

it("separates a disabled or cooling status click from required system refresh", async () => {
  read.mockRejectedValueOnce(Object.assign(new Error("Capacity"), { status: 429, retryAfterMs: Infinity }))
    .mockResolvedValue({ status: "done" });
  const show = (enabled: boolean) => <QueryClientProvider client={client}><Probe enabled={enabled} /></QueryClientProvider>;
  const view = render(show(false));
  await tick(1);
  const button = screen.getByRole("button", { name: "Check status" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(read).not.toHaveBeenCalled();
  view.rerender(show(true));
  await tick(1_000_000);
  expect(button).toBeDisabled();
  expect(read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "System refresh" }));
  await tick(1);
  expect(read).toHaveBeenCalledTimes(2);
  expect(button).toBeEnabled();
});

it("does not cancel or duplicate a read when multiple controls check the same key", async () => {
  read.mockResolvedValueOnce({ status: "done" }).mockImplementation(() => new Promise(() => {}));
  render(<QueryClientProvider client={client}><Probe /><Probe label="Other status" /></QueryClientProvider>);
  await tick(1);
  expect(read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  fireEvent.click(screen.getByRole("button", { name: "Other status" }));
  expect(read).toHaveBeenCalledTimes(2);
});

it("leaves a different key and ordinary failed status recovery available", async () => {
  read.mockRejectedValueOnce(Object.assign(new Error("Capacity"), { status: 429 }))
    .mockRejectedValueOnce(Object.assign(new Error("Unavailable"), { status: 404 }))
    .mockRejectedValueOnce(Object.assign(new Error("Unavailable"), { status: 404 }))
    .mockResolvedValue({ status: "done" });
  const show = (id: string) => <QueryClientProvider client={client}><Probe id={id} /></QueryClientProvider>;
  const view = render(show("first"));
  const button = () => screen.getByRole("button", { name: "Check status" });
  await tick(1);
  expect(button()).toBeDisabled();
  view.rerender(show("second"));
  await tick(1002);
  expect(read).toHaveBeenCalledTimes(3);
  expect(button()).toBeEnabled();
  fireEvent.click(button());
  await tick(1);
  expect(read).toHaveBeenCalledTimes(4);
  expect(client.getQueryState(["first"])?.error).toMatchObject({ status: 429 });
  expect(client.getQueryData(["second"])).toEqual({ status: "done" });
});

it("reads fresh successful cached status when the same owner reopens a review", async () => {
  read.mockResolvedValue({ status: "done" });
  const view = render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(1); expect(screen.getByText("done")).toBeInTheDocument();
  view.unmount(); read.mockResolvedValue({ status: "pending" });
  render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  await tick(1); expect(read).toHaveBeenCalledTimes(2); expect(screen.getByText("pending")).toBeInTheDocument();
});
