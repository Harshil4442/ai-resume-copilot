import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const wire = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ apiGet: wire }));
import { useResumeVersionOptions } from "./resumeVersionOptions";

let client: QueryClient;
function row(id: string, resume = 1) { return { id, resume_id: resume, label: id, version_number: 1, approval_state: "approved", source_preserving_edits: true, opportunity_id: "role", created_at: "2026-11-05T00:00:00Z", submitted_at: null }; }
function Harness({ owner = "A", resume = "1", current = "chosen" }: { owner?: string; resume?: string; current?: string }) {
  const versions = useResumeVersionOptions({ owner, resumeId: resume, currentId: current, enabled: true });
  return <><p>Selected ID: {current}</p>{versions.isError ? <p role="alert">Version unavailable; selection unchanged</p> : null}<ul>{versions.items.map((version) => <li key={version.id}>{version.label}</li>)}</ul><button onClick={versions.more} disabled={!versions.hasMore || versions.isFetching}>Older</button><button onClick={versions.newest} disabled={!versions.older}>Newest</button></>;
}
function tree(child: React.ReactNode) { return <QueryClientProvider client={client}>{child}</QueryClientProvider>; }
beforeEach(() => { client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); wire.mockReset(); });
afterEach(() => { cleanup(); client.clear(); });

it("retains the exact approved selection outside each bounded page with no full version bodies", async () => {
  wire.mockImplementation((url: string) => Promise.resolve({ items: Array.from({ length: 20 }, (_, index) => row(`${url.includes("cursor=") ? "older" : "new"}-${index}`)), next_cursor: url.includes("cursor=") ? null : "opaque", selected_version: row("chosen") }));
  render(tree(<Harness />));
  await screen.findByText("chosen");
  expect(screen.getAllByRole("listitem")).toHaveLength(21);
  expect(wire).toHaveBeenCalledTimes(1);
  expect(wire.mock.calls[0][0]).toBe("/v1/resume-versions/page?resume_id=1&limit=20&approval_state=approved&current_id=chosen");
  fireEvent.click(screen.getByRole("button", { name: "Older" }));
  await screen.findByText("older-0");
  expect(screen.queryByText("new-0")).toBeNull();
  expect(screen.getByText("chosen")).toBeVisible();
  expect(screen.getAllByRole("listitem")).toHaveLength(21);
  expect(wire).toHaveBeenCalledTimes(2);
  expect(wire.mock.calls[1][0]).toContain("cursor=opaque&current_id=chosen");
});

it("does not replace an unavailable selected ID with the first row", async () => {
  wire.mockResolvedValue({ items: [row("different")], next_cursor: null, selected_version: null });
  render(tree(<Harness />));
  await screen.findByRole("alert", {}, { timeout: 2500 });
  expect(screen.getByText("Selected ID: chosen")).toBeVisible();
  expect(screen.queryByText("different")).toBeNull();
});

it("aborts prior owner/source requests and rejects late cross-source metadata", async () => {
  let finish!: (value: unknown) => void;
  let firstSignal: AbortSignal | undefined;
  wire.mockImplementation((url: string, signal?: AbortSignal) => {
    if (url.includes("resume_id=1")) { firstSignal = signal; return new Promise((resolve) => { finish = resolve; }); }
    return Promise.resolve({ items: [row("B version", 2)], selected_version: null, next_cursor: null });
  });
  const view = render(tree(<Harness />));
  await waitFor(() => expect(finish).toBeDefined());
  view.rerender(tree(<Harness owner="B" resume="2" current="" />));
  await screen.findByText("B version");
  expect(firstSignal?.aborted).toBe(true);
  await act(async () => finish({ items: [row("A private version")], selected_version: row("chosen"), next_cursor: null }));
  expect(screen.queryByText("A private version")).toBeNull();
  expect(wire).toHaveBeenCalledTimes(2);
});
