import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useResumeOptions } from "../lib/resumeOptions";
import { ResumeSelector, ResumeVersionMetadata } from "./ResumeSelector";

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../lib/api", () => ({ apiGet: api.get }));
vi.mock("../lib/readQueryPolicy", () => ({ retryRead: () => false }));
const item = (id: number) => ({ id, filename: `owned-${id}.pdf`, created_at: "2026-11-01T00:00:00Z", source_available: true, source_format: "pdf" });
const page = (ids: number[], cursor: string | null = null) => ({ resumes: ids.map(item), next_cursor: cursor, selected_resume: null });
let client: QueryClient;
function Picker({ current = "", owner = "one" }: { current?: string; owner?: string }) {
  const [value, setValue] = useState(current);
  const catalog = useResumeOptions({ selectedId: value, ownerKey: owner });
  return <><ResumeSelector catalog={catalog} value={value} onChange={setValue} /><output>{catalog.selected?.filename}</output></>;
}
function tree(current = "", owner = "one") { return <QueryClientProvider client={client}><Picker key={owner} current={current} owner={owner} /></QueryClientProvider>; }
beforeEach(() => { client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); api.get.mockReset(); });
afterEach(() => { cleanup(); client.clear(); });

it("reads only the first page until an explicit older action and retains the chosen row across pages", async () => {
  api.get.mockImplementation(async (path: string) => path.includes("cursor=") ? page([2, 1]) : path.includes("current_id=20") ? { ...page([]), selected_resume: item(20) } : page([20, 19], "older/cursor"));
  render(tree());
  await screen.findByRole("option", { name: "owned-20.pdf · #20" });
  expect(api.get).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Load older resumes" }));
  await screen.findByRole("option", { name: "owned-1.pdf · #1" });
  await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("20"));
  expect(screen.getByText("owned-20.pdf", { selector: "output" })).toBeVisible();
  expect(api.get.mock.calls.filter(([path]) => String(path).includes("cursor="))).toEqual([["/resume/page?limit=20&cursor=older%2Fcursor", expect.any(AbortSignal)]]);
  expect(screen.queryByRole("button", { name: "Load older resumes" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Newest resumes" }));
  await screen.findByRole("option", { name: "owned-19.pdf · #19" });
  expect(screen.getByRole("combobox")).toHaveValue("20");
});

it("hydrates an older current selection with one owned lookup, without walking intervening pages", async () => {
  api.get.mockImplementation(async (path: string) => path.includes("current_id=1") ? { ...page([20]), selected_resume: item(1) } : page([20, 19], "older"));
  render(tree("1"));
  await screen.findByRole("option", { name: "owned-1.pdf · #1" });
  expect(screen.getByRole("combobox")).toHaveValue("1");
  expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/resume/page?limit=20", "/resume/page?limit=1&current_id=1"]);
});

it("does not replace an unavailable current selection with another owned row", async () => {
  api.get.mockImplementation(async (path: string) => { if (path.includes("current_id=")) throw new Error("Selected resume is unavailable"); return page([20]); });
  render(tree("1"));
  await screen.findByRole("alert");
  expect(screen.getByRole("combobox")).toHaveValue("1");
  expect(screen.getByRole("option", { name: /Selected resume · #1 · unavailable/ })).toBeDisabled();
  expect(screen.getByText("", { selector: "output" })).toBeEmptyDOMElement();
});

it("shows loading and empty states, and retries a failed page explicitly", async () => {
  let resolve!: (value: ReturnType<typeof page>) => void;
  api.get.mockImplementationOnce(() => new Promise((finish) => { resolve = finish; }));
  render(tree());
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(screen.getByText("Loading saved resumes…")).toBeVisible();
  await act(async () => resolve(page([])));
  expect(await screen.findByText("No saved resumes on this page.")).toBeVisible();
  api.get.mockRejectedValueOnce(new Error("Temporary read failure"));
  await act(async () => { await client.invalidateQueries({ queryKey: ["resumes"] }); });
  expect(await screen.findByRole("alert")).toHaveTextContent("Temporary read failure");
  api.get.mockResolvedValueOnce(page([3]));
  fireEvent.click(screen.getByRole("button", { name: "Retry resumes" }));
  await screen.findByRole("option", { name: "owned-3.pdf · #3" });
});

it("aborts an unmounted owner's request and ignores late rows on owner change", async () => {
  let finish!: (value: ReturnType<typeof page>) => void;
  let oldSignal!: AbortSignal;
  api.get.mockImplementationOnce((_path: string, signal: AbortSignal) => { oldSignal = signal; return new Promise((resolve) => { finish = resolve; }); }).mockResolvedValue(page([2]));
  const rendered = render(tree("", "one"));
  await waitFor(() => expect(api.get).toHaveBeenCalledOnce());
  rendered.rerender(tree("", "two"));
  await screen.findByRole("option", { name: "owned-2.pdf · #2" });
  expect(oldSignal.aborted).toBe(true);
  await act(async () => finish(page([1])));
  expect(screen.queryByRole("option", { name: "owned-1.pdf · #1" })).toBeNull();
});

it("reads an older version source only on demand and retains its exact metadata", async () => {
  api.get.mockResolvedValue({ ...page([]), selected_resume: item(1) });
  render(<QueryClientProvider client={client}><ResumeVersionMetadata resumeId={1}>{(resume) => <output>{resume?.filename}</output>}</ResumeVersionMetadata></QueryClientProvider>);
  expect(api.get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Load saved version source details" }));
  await screen.findByText("owned-1.pdf");
  expect(api.get).toHaveBeenCalledExactlyOnceWith("/resume/page?limit=1&current_id=1", expect.any(AbortSignal));
});

it("cancels an older-current lookup and ignores its late metadata after an explicit selection change", async () => {
  let finish!: (value: Omit<ReturnType<typeof page>, "selected_resume"> & { selected_resume: ReturnType<typeof item> }) => void;
  let signal!: AbortSignal;
  api.get.mockImplementation((path: string, requestSignal: AbortSignal) => {
    if (path.includes("current_id=1")) { signal = requestSignal; return new Promise((resolve) => { finish = resolve; }); }
    return Promise.resolve(page([20]));
  });
  render(tree("1"));
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  // While hydration is pending, changing the controlled value directly still
  // cannot let the earlier request replace the next exact selection.
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "20" } });
  await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("20"));
  expect(signal.aborted).toBe(true);
  await act(async () => finish({ ...page([]), selected_resume: item(1) }));
  expect(screen.getByRole("combobox")).toHaveValue("20");
  expect(screen.getByText("owned-20.pdf", { selector: "output" })).toBeVisible();
});

it("invalidates page and current-row metadata without silently selecting a newer upload or a deleted source", async () => {
  let deleted = false;
  api.get.mockImplementation(async (path: string) => {
    if (path.includes("current_id=1")) { if (deleted) throw new Error("Selected resume is unavailable"); return { ...page([]), selected_resume: item(1) }; }
    return page([deleted ? 30 : 20]);
  });
  render(tree("1"));
  await screen.findByRole("option", { name: "owned-1.pdf · #1" });
  deleted = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["resumes"] }); });
  await screen.findByRole("option", { name: "owned-30.pdf · #30" });
  expect(screen.getByRole("combobox")).toHaveValue("1");
  expect(await screen.findByRole("alert")).toHaveTextContent("Selected resume is unavailable");
  expect(screen.queryByRole("option", { name: "owned-1.pdf · #1" })).toBeNull();
});


it("retains an explicit retry after cached history metadata refetch fails and keeps source actions closed until fresh success", async () => {
  const reply = (filename: string) => ({ ...page([]), selected_resume: { ...item(1), filename } });
  api.get.mockResolvedValueOnce(reply("first.pdf"));
  render(<QueryClientProvider client={client}><ResumeVersionMetadata resumeId={1}>{(resume, state) => <><output>{resume?.filename}</output><button disabled={!resume || state !== "ready"}>Download checked source</button></>}</ResumeVersionMetadata></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Load saved version source details" }));
  await screen.findByText("first.pdf");
  expect(screen.getByRole("button", { name: "Download checked source" })).toBeEnabled();
  api.get.mockRejectedValueOnce(Object.assign(new Error("Temporary metadata refusal"), { status: 503 }));
  await act(async () => { await client.invalidateQueries({ queryKey: ["resumes", "selection"] }); });
  expect(await screen.findByRole("alert")).toHaveTextContent("Source details are unavailable");
  expect(screen.queryByText("first.pdf")).toBeNull();
  expect(screen.getByRole("button", { name: "Download checked source" })).toBeDisabled();
  expect(api.get).toHaveBeenCalledTimes(2);
  api.get.mockResolvedValueOnce(reply("fresh.pdf"));
  fireEvent.click(screen.getByRole("button", { name: "Load saved version source details" }));
  await screen.findByText("fresh.pdf");
  expect(screen.getByRole("button", { name: "Download checked source" })).toBeEnabled();
  expect(api.get).toHaveBeenCalledTimes(3);
});
