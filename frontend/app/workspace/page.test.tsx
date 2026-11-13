import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), push: vi.fn(),
  session: { status: "authenticated", data: { user: { id: "owner-one" } } } as {
    status: string; data: { user: { id: string } } | null;
  } }));
vi.mock("../../lib/api", () => ({ apiGet: api.get, apiPostJson: api.post }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: api.push }) }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
vi.mock("next-auth/react", () => ({ useSession: () => api.session }));
import Workspace from "./page";

let client: QueryClient;
const row = (id: string, title = `Role ${id}`) => ({ id, title, company: "Owned company", location: "India",
  stage: "saved", priority: "medium", next_action: null, updated_at: "2026-11-05T00:00:00Z" });
const page = (items = [row("201")], changes = {}) => ({ schema_version: "opportunity-page-v1", items, total: 201,
  total_owned: 250, active_count: 220, interview_count: 45, next_cursor: "older-201", ...changes });
function tree() { return <QueryClientProvider client={client}><Workspace /></QueryClientProvider>; }
beforeEach(() => {
  api.session = { status: "authenticated", data: { user: { id: "owner-one" } } };
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
});
afterEach(() => { cleanup(); client?.clear(); vi.resetAllMocks(); });
it("creates an opportunity with the explicitly chosen older resume without fetching all pages", async () => {
  const user = userEvent.setup();
  const resume = (id: number) => ({ id, filename: `owned-${id}.pdf`, created_at: "2026-11-01T00:00:00Z", source_available: true, source_format: "pdf" });
  api.get.mockImplementation(async (path: string) => path.startsWith("/v1/opportunities/page?")
    ? path.includes("cursor=") ? page([row("1")], { next_cursor: null }) : page()
    : path.includes("cursor=") ? { resumes: [resume(1)], next_cursor: null, selected_resume: null }
    : { resumes: [resume(20)], next_cursor: "older", selected_resume: null });
  api.post.mockResolvedValue({ id: "owned_opportunity", source: "manual", resume_id: 1 });
  render(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  expect(api.get.mock.calls.filter(([path]) => String(path).startsWith("/resume/page"))).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Older opportunities" }));
  await screen.findByRole("heading", { name: "Role 1" });
  const invalidation = vi.spyOn(client, "invalidateQueries");
  await user.click(screen.getAllByRole("button", { name: "Add opportunity" })[0]);
  await screen.findByRole("option", { name: "owned-20.pdf · #20" });
  expect(api.get.mock.calls.filter(([path]) => String(path).startsWith("/resume/page"))).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Load older resumes" }));
  await screen.findByRole("option", { name: "owned-1.pdf · #1" });
  await user.selectOptions(screen.getByRole("combobox", { name: "Resume" }), "1");
  await user.type(screen.getByRole("textbox", { name: "Role title" }), "Software Engineer");
  await user.type(screen.getByRole("textbox", { name: "Job description" }), "A complete synthetic role description for an owned local test.");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledExactlyOnceWith("/v1/opportunities", expect.objectContaining({ resume_id: 1 })));
  await waitFor(() => expect(api.push).toHaveBeenCalledExactlyOnceWith("/workspace/owned_opportunity"));
  expect(invalidation).toHaveBeenCalledWith({ queryKey: ["opportunities"], refetchType: "none" });
  await screen.findByText("Viewing newest results");
});

it("loads only20 metadata rows, uses whole-owner counts, and visits older results only on request", async () => {
  const user = userEvent.setup();
  client.setDefaultOptions({ queries: { retry: false, gcTime: 300_000, staleTime: 30_000 } });
  let changedHead = false;
  api.get.mockImplementation(async (path: string) => path.includes("cursor=")
    ? page([row("1", "An older role beyond the previous200 limit")], { next_cursor: null })
    : changedHead ? page([row("202", "Newly added head role")]) : page());
  render(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  expect(screen.getByText("220 active roles and 45 in interview stage.")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 201 matching opportunities · 250 saved in total.");
  expect(api.get).toHaveBeenCalledExactlyOnceWith("/v1/opportunities/page?limit=20", expect.any(AbortSignal));
  await user.click(screen.getByRole("button", { name: "Older opportunities" }));
  await screen.findByRole("heading", { name: "An older role beyond the previous200 limit" });
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "Older opportunities" })).toBeDisabled();
  changedHead = true;
  await user.click(screen.getByRole("button", { name: "Newest opportunities" }));
  await screen.findByRole("heading", { name: "Newly added head role" });
  expect(api.get.mock.calls.map(([path]) => path)).toEqual([
    "/v1/opportunities/page?limit=20", "/v1/opportunities/page?limit=20&cursor=older-201",
    "/v1/opportunities/page?limit=20",
  ]);
});

it("resets the cursor atomically for stage and archived filters", async () => {
  const user = userEvent.setup();
  api.get.mockImplementation(async (path: string) => path.includes("cursor=") ? page([row("1")], { next_cursor: null }) : page());
  render(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  await user.click(screen.getByRole("button", { name: "Older opportunities" }));
  await screen.findByText("Viewing older results");
  await user.click(screen.getByRole("tab", { name: "Interviewing" }));
  await waitFor(() => expect(api.get.mock.calls.at(-1)?.[0]).toBe("/v1/opportunities/page?limit=20&stage=interviewing"));
  await screen.findByText("Viewing newest results");
  await user.click(screen.getByRole("button", { name: "Older opportunities" }));
  await screen.findByText("Viewing older results");
  await user.click(screen.getByRole("checkbox", { name: "Include archived opportunities" }));
  await waitFor(() => expect(api.get.mock.calls.at(-1)?.[0]).toBe("/v1/opportunities/page?limit=20&stage=interviewing&include_archived=true"));
  expect(api.get.mock.calls.some(([path]) => String(path).includes("include_archived=true") && String(path).includes("cursor="))).toBe(false);
});

it("debounces server search, cancels the client read, and ignores an old late reply", async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  api.get.mockImplementation((path: string) => path.includes("search=")
    ? Promise.resolve(page([row("1", "Older matching role")], { total: 1, next_cursor: null }))
    : path.includes("cursor=") ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(page()));
  render(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  await user.click(screen.getByRole("button", { name: "Older opportunities" }));
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  const oldSignal = api.get.mock.calls[1][1] as AbortSignal;
  fireEvent.change(screen.getByRole("textbox", { name: "Search opportunities" }), { target: { value: "  matching  " } });
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(oldSignal.aborted).toBe(true); // Client cancellation only; BFF transport is checked separately.
  await screen.findByRole("heading", { name: "Older matching role" });
  expect(api.get.mock.calls[2][0]).toBe("/v1/opportunities/page?limit=20&search=matching");
  await act(async () => { finish(page([row("old", "Stale previous search")])); });
  expect(screen.queryByRole("heading", { name: "Stale previous search" })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 1 matching opportunities");
});

it("resets owner/window state and cannot show a late prior-owner page", async () => {
  let finish!: (value: unknown) => void;
  api.get.mockImplementation(() => api.session.data?.user.id === "owner-one"
    ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(page([row("two", "Second owner role")])));
  const view = render(tree());
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
  const oldSignal = api.get.mock.calls[0][1] as AbortSignal;
  api.session = { status: "authenticated", data: { user: { id: "owner-two" } } };
  view.rerender(tree());
  await screen.findByRole("heading", { name: "Second owner role" });
  expect(oldSignal.aborted).toBe(true);
  await act(async () => { finish(page([row("one", "Prior owner private role")])); });
  expect(screen.queryByRole("heading", { name: "Prior owner private role" })).not.toBeInTheDocument();
  expect(client.getQueryCache().getAll().some((query) => query.queryKey[2] === "authenticated:owner-two")).toBe(true);
});

it("does not apply a late create callback after its owner workspace unmounts", async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  api.get.mockImplementation(async (path: string) => path.startsWith("/resume/page")
    ? { resumes: [], next_cursor: null, selected_resume: null }
    : page([row(api.session.data?.user.id || "signed-out")]));
  api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const invalidation = vi.spyOn(client, "invalidateQueries");
  const view = render(tree());
  await screen.findByRole("heading", { name: "Role owner-one" });
  await user.click(screen.getByRole("button", { name: "Add opportunity" }));
  await user.type(screen.getByRole("textbox", { name: "Role title" }), "Software Engineer");
  await user.type(screen.getByRole("textbox", { name: "Job description" }), "A complete synthetic role description for an owned local test.");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
  api.session = { status: "authenticated", data: { user: { id: "owner-two" } } };
  view.rerender(tree());
  await screen.findByRole("heading", { name: "Role owner-two" });
  await act(async () => { finish({ id: "old-owner-created", source: "manual", resume_id: null }); });
  expect(api.push).not.toHaveBeenCalled();
  expect(invalidation).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "Role owner-two" })).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("hides cached rows and counts on a current error without fallback or auto-retry", async () => {
  api.get.mockResolvedValueOnce(page()).mockRejectedValue(Object.assign(new Error("Synthetic capacity refusal"), { status: 503 }));
  render(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  await act(async () => { await client.invalidateQueries({ queryKey: ["opportunities"] }); });
  expect(await screen.findByRole("alert")).toHaveTextContent("Synthetic capacity refusal");
  expect(screen.queryByRole("heading", { name: "Role 201" })).not.toBeInTheDocument();
  expect(screen.getByText("Opportunity totals are unavailable.")).toBeVisible();
  expect(api.get).toHaveBeenCalledTimes(2);
});

it("recovers a refused older cursor only when the user requests a fresh newest page", async () => {
  const user = userEvent.setup();
  client.setDefaultOptions({ queries: { retry: false, gcTime: 300_000, staleTime: 30_000 } });
  let changedHead = false;
  api.get.mockImplementation(async (path: string) => {
    if (path.includes("cursor=")) throw Object.assign(new Error("Synthetic saved page is no longer valid"), { status: 400 });
    return changedHead ? page([row("202", "Fresh newest after refused cursor")]) : page();
  });
  render(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  await user.click(screen.getByRole("button", { name: "Older opportunities" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Synthetic saved page is no longer valid");
  expect(screen.queryByRole("heading", { name: "Role 201" })).not.toBeInTheDocument();
  expect(api.get).toHaveBeenCalledTimes(2);
  changedHead = true;
  await user.click(screen.getByRole("button", { name: "Return to newest opportunities" }));
  await screen.findByRole("heading", { name: "Fresh newest after refused cursor" });
  expect(api.get.mock.calls.map(([path]) => path)).toEqual([
    "/v1/opportunities/page?limit=20", "/v1/opportunities/page?limit=20&cursor=older-201",
    "/v1/opportunities/page?limit=20",
  ]);
});

it("does not request or expose a saved owner's page while the session is loading or signed out", async () => {
  api.session = { status: "loading", data: null };
  api.get.mockResolvedValue(page());
  const view = render(tree());
  expect(api.get).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Add opportunity" })).toBeDisabled();
  api.session = { status: "authenticated", data: { user: { id: "owner-one" } } };
  view.rerender(tree());
  await screen.findByRole("heading", { name: "Role 201" });
  api.session = { status: "unauthenticated", data: null };
  view.rerender(tree());
  expect(screen.queryByRole("heading", { name: "Role 201" })).not.toBeInTheDocument();
  expect(api.get).toHaveBeenCalledTimes(1);
});

it("distinguishes no matches from having no saved opportunities", async () => {
  api.get.mockResolvedValue(page([], { total: 0, next_cursor: null }));
  render(tree());
  await screen.findByText("No opportunities match this page or these filters");
  expect(screen.queryByText("Add your first target role")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("0 of 0 matching opportunities · 250 saved in total.");
});
