import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Workspace } from "./career";

const wire = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ apiGet: wire }));
import { resetWorkspaceAfterSourceConnection, useWorkspace, useWorkspacePage, useWorkspaceResumes, workspaceConnectionKey, workspaceKey, type WorkspaceTab, type WorkspaceCollection } from "./workspacePages";
import { WorkspaceItemDetails, WorkspacePageControls } from "../components/career/WorkspaceHistory";

let client: QueryClient;
const stamp = "2026-11-05T00:00:00Z";
function seed(owner: string, source = 800, tab = "resume"): Workspace {
  const versions = Array.from({ length: 20 }, (_, index) => ({ id: `${owner}-v${index}`, resume_id: source, opportunity_id: "role", version_number: 200-index, label: `${owner} version${index}`, approval_state: "draft", submitted_at: null, created_at: stamp }));
  return { opportunity: { id: "role", resume_id: source, title: `${owner} role`, company: "Owned fixture", location: "Remote", archived_at: null, compensation: null, deadline_at: null, latest_analysis_run_id: null, latest_match_id: null, next_action: null, outcome: null, outcome_at: null, source_url: null, source: "manual", stage: "saved", priority: "medium", created_at: stamp, updated_at: stamp },
    resumes: { resumes: [{ id: 999, filename: "latest.pdf", source_available: true, source_format: "pdf", created_at: stamp }], next_cursor: "source-older", selected_resume: { id: source, filename: `${owner}-selected.pdf`, source_available: true, source_format: "pdf", created_at: stamp } },
    pages: { resume_versions: { items: versions, next_cursor: "versions-older" }, evidence: tab === "resume" ? { items: [], next_cursor: "facts-older" } : null },
    match: null, match_source_state: "none", submitted_resume_version_id: "outside-first-page", submitted_resume_version: null, selected_resume_version: null, approved_evidence_count: tab === "resume" ? 81 : null };
}
function Harness({ owner = "A", role = "role", tab = "resume" as WorkspaceTab, collection = "resume_versions" as WorkspaceCollection }: { owner?: string; role?: string; tab?: WorkspaceTab; collection?: WorkspaceCollection }) {
  const workspace = useWorkspace(owner, role, tab, true);
  const page = useWorkspacePage({ owner, opportunity: role, tab, source: workspace.data?.opportunity.resume_id, collection, seed: workspace.data?.pages.resume_versions,
    enabled: workspace.isSuccess, refreshing: workspace.isFetching, refresh: () => workspace.refetch() });
  const resumes = useWorkspaceResumes({ owner, opportunity: role, seed: workspace.data?.resumes, loading: workspace.isPending, fetching: workspace.isFetching, error: workspace.error, refresh: () => workspace.refetch() });
  return <><p>{workspace.data?.opportunity.title || "Pending workspace"}</p><p>{resumes.selected?.filename || "Pending source"}</p><p>{workspace.data?.approved_evidence_count ?? "No evidence count on this tab"}</p><ul>{page.items.map((version) => <li key={version.id}>{version.label}</li>)}</ul><WorkspacePageControls page={page} label="versions" /></>;
}
function tree(child: React.ReactNode) { return <QueryClientProvider client={client}>{child}</QueryClientProvider>; }
beforeEach(() => { client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); wire.mockReset(); });
afterEach(() => { cleanup(); client.clear(); });

it("uses one metadata bootstrap, preserves off-page source and aggregate count, and advances one bounded window only on request", async () => {
  wire.mockImplementation((url: string) => url.includes("workspace?") ? Promise.resolve(seed("A")) : Promise.resolve({ items: Array.from({ length: 20 }, (_, index) => ({ ...seed("A").pages.resume_versions.items[0], id: `old${index}`, label: `Older version${index}` })), next_cursor: "third-page" }));
  render(tree(<Harness />));
  await screen.findByText("A-selected.pdf");
  expect(screen.getByText("81")).toBeVisible();
  expect(screen.getByRole("group", { name: "versions pages" })).toBeVisible();
  expect(screen.getAllByRole("listitem")).toHaveLength(20);
  expect(wire).toHaveBeenCalledTimes(1);
  expect(wire.mock.calls[0][0]).toBe("/v1/opportunities/role/workspace?tab=resume&limit=20");
  expect(wire.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
  fireEvent.click(screen.getByRole("button", { name: "Older versions" }));
  await screen.findByText("Older version0");
  expect(screen.getAllByRole("listitem")).toHaveLength(20);
  expect(screen.queryByText("A version0")).toBeNull();
  expect(screen.getByText("A-selected.pdf")).toBeVisible();
  expect(wire).toHaveBeenCalledTimes(2);
  expect(wire.mock.calls[1][0]).toBe("/v1/opportunities/role/workspace/resume_versions?limit=20&cursor=versions-older");
  fireEvent.click(screen.getByRole("button", { name: "Newest versions" }));
  await screen.findByText("A version0");
  await waitFor(() => expect(wire).toHaveBeenCalledTimes(3));
});

it("aborts a pending old page on tab change and never shows its late response", async () => {
  let finish!: (page: unknown) => void;
  let pendingSignal: AbortSignal | undefined;
  wire.mockImplementation((url: string, signal?: AbortSignal) => {
    if (url.includes("workspace?")) return Promise.resolve(seed("A", 800, url.includes("tab=resume") ? "resume" : "activity"));
    pendingSignal = signal;
    return new Promise((resolve) => { finish = resolve; });
  });
  const view = render(tree(<Harness />));
  await screen.findByText("A version0");
  fireEvent.click(screen.getByRole("button", { name: "Older versions" }));
  await waitFor(() => expect(finish).toBeDefined());
  view.rerender(tree(<Harness tab="activity" />));
  await screen.findByText("No evidence count on this tab");
  await waitFor(() => expect(pendingSignal?.aborted).toBe(true));
  await act(async () => finish({ items: [{ id: "late", label: "Late old response" }], next_cursor: null }));
  expect(screen.queryByText("Late old response")).toBeNull();
  expect(wire.mock.calls.filter(([url]) => String(url).includes("/workspace/resume_versions?"))).toHaveLength(1);
  expect(wire.mock.calls).toHaveLength(3);
});

it("separates owner and opportunity caches instead of keeping the previous owner's labels", async () => {
  wire.mockImplementation((url: string) => Promise.resolve(seed(url.includes("other-role") ? "B" : "A")));
  const view = render(tree(<Harness />));
  await screen.findByText("A-selected.pdf");
  view.rerender(tree(<Harness owner="B" role="other-role" />));
  expect(screen.queryByText("A-selected.pdf")).toBeNull();
  expect(screen.queryByText("A version0")).toBeNull();
  await screen.findByText("B-selected.pdf");
  expect(wire).toHaveBeenCalledTimes(2);
});

it("opens only one requested full body and refuses a mismatched row", async () => {
  wire.mockResolvedValue({ id: "wrong", note: "Must not render" });
  render(tree(<><WorkspaceItemDetails<{ id: string; note: string }> owner="A" opportunity="role" collection="activity" id="first" label="Open first">{(item) => <p>{item.note}</p>}</WorkspaceItemDetails><WorkspaceItemDetails<{ id: string; note: string }> owner="A" opportunity="role" collection="activity" id="second" label="Open second">{(item) => <p>{item.note}</p>}</WorkspaceItemDetails></>));
  expect(wire).not.toHaveBeenCalled();
  const details = screen.getByText("Open first").parentElement as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event("toggle"));
  await screen.findByRole("alert", {}, { timeout: 2500 });
  expect(screen.queryByText("Must not render")).toBeNull();
  expect(wire.mock.calls.every(([url]) => String(url).endsWith("/activity/first"))).toBe(true);
  expect(wire.mock.calls.length).toBeLessThanOrEqual(2);
});

it("clears only the owned workspace after upload connection and retains a non-authorizing pending hint", async () => {
  client.setQueryData([...workspaceKey("A", "role"), "tab", "resume"], seed("A"));
  client.setQueryData([...workspaceKey("A", "other-role"), "tab", "resume"], seed("A"));
  client.setQueryData([...workspaceKey("B", "role"), "tab", "resume"], seed("B"));
  await resetWorkspaceAfterSourceConnection(client, "A", "role", { resumeId: 42, title: "Connected role" });
  expect(client.getQueriesData({ queryKey: workspaceKey("A", "role") })).toEqual([]);
  expect(client.getQueriesData({ queryKey: workspaceKey("A", "other-role") })).toHaveLength(1);
  expect(client.getQueriesData({ queryKey: workspaceKey("B", "role") })).toHaveLength(1);
  expect(client.getQueryData(workspaceConnectionKey("A", "role"))).toEqual({ resumeId: 42, title: "Connected role" });
  expect(client.getQueryData(workspaceConnectionKey("B", "role"))).toBeUndefined();
  expect(wire).not.toHaveBeenCalled();
});

it("starts a new collection at its first window rather than reusing another collection's cursor", async () => {
  wire.mockImplementation((url: string) => url.includes("workspace?") ? Promise.resolve(seed("A")) : Promise.resolve({ items: [{ ...seed("A").pages.resume_versions.items[0], label: "Older row" }], next_cursor: "another-cursor" }));
  const view = render(tree(<Harness />));
  await screen.findByText("A version0");
  fireEvent.click(screen.getByRole("button", { name: "Older versions" }));
  await screen.findByText("Older row");
  view.rerender(tree(<Harness collection="activity" />));
  await screen.findByText("A version0");
  expect(screen.queryByText("Older row")).toBeNull();
  expect(wire.mock.calls.some(([url]) => String(url).includes("/workspace/activity?"))).toBe(false);
  expect(wire).toHaveBeenCalledTimes(2);
});
