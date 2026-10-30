import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ get: vi.fn(), owner: "owner-a", status: "authenticated" }));
vi.mock("../../lib/api", async (original) => ({ ...await original<typeof import("../../lib/api")>(), apiGet: state.get }));
vi.mock("next-auth/react", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children,
  useSession: () => ({ status: state.status, data: state.status === "authenticated" ? { user: { id: state.owner } } : null }) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
import Nav from "../../components/Nav";
import SessionProviderWrapper from "../../components/SessionProviderWrapper";
import { ApiError } from "../../lib/api";
import { ownedDashboardFixture } from "../../lib/fixtures/ownedDashboard";
import { ownedBootstrapFixture } from "../../lib/fixtures/ownedBootstrap";
import { ownedDashboardKey } from "../../lib/ownedDashboardClient";
import Dashboard from "./page";
let client: QueryClient;
function tree() { return <QueryClientProvider client={client}><Nav /><Dashboard /></QueryClientProvider>; }
beforeEach(() => {
  state.owner = "owner-a"; state.status = "authenticated";
  client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  state.get.mockReset().mockResolvedValue(ownedDashboardFixture());
});

it("isolates a late dashboard response across actual owner-boundary remount and logout", async () => {
  let pending = false, resolveOld!: (value: unknown) => void, oldSignal!: AbortSignal;
  state.get.mockImplementation((_path: string, signal: AbortSignal) => {
    const value = ownedDashboardFixture(); value.opportunities!.items[0].title = state.owner;
    if (state.owner === "owner-a" && pending) { oldSignal = signal; return new Promise((resolve) => { resolveOld = resolve; }); }
    return Promise.resolve(value);
  });
  const ownedTree = () => <SessionProviderWrapper><Nav /><Dashboard /></SessionProviderWrapper>;
  const view = render(ownedTree()); await screen.findByText("owner-a");
  pending = true;
  act(() => { window.dispatchEvent(new Event("refresh_unified_credits")); });
  await waitFor(() => expect(resolveOld).toBeDefined());
  state.owner = "owner-b"; view.rerender(ownedTree());
  await screen.findByText("owner-b"); expect(screen.queryByText("owner-a")).toBeNull(); expect(oldSignal.aborted).toBe(true);
  await act(async () => { resolveOld(ownedDashboardFixture()); });
  expect(screen.getByText("owner-b")).toBeVisible(); expect(screen.queryByText("Owned role")).toBeNull();
  state.status = "unauthenticated"; view.rerender(ownedTree());
  expect(screen.queryByText("owner-b")).toBeNull(); expect(screen.getByRole("link", { name: "Sign in to continue to your workspace." })).toBeVisible();
});
afterEach(() => { cleanup(); client.clear(); });

it("coalesces the actual Nav and Dashboard to one owned snapshot without losing displayed resume signals", async () => {
  render(tree());
  await screen.findByText("Owned role");
  expect(screen.getByText("Owned follow-up")).toBeVisible();
  expect(screen.getByText("50%")).toBeVisible();
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
  await act(async () => { window.dispatchEvent(new Event("refresh_unified_credits")); });
  await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2));
  expect(state.get.mock.calls[1][0]).toBe("/auth/dashboard");
});

it("shows unavailable components rather than invented zero counts, empty workspaces or progress", async () => {
  const value = ownedDashboardFixture(); value.summary = null; value.opportunities = null; value.reminders = null;
  value.unavailable = ["summary", "opportunities", "reminders"];
  state.get.mockResolvedValue(value); render(tree());
  await screen.findByText("Progress unavailable");
  expect(screen.getByText("Opportunities are temporarily unavailable.")).toBeVisible();
  expect(screen.getByText("Reminders are temporarily unavailable.")).toBeVisible();
  expect(screen.queryByText("No active opportunities")).toBeNull();
  expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(2);
  expect(state.get).toHaveBeenCalledTimes(1);
});

it("keeps permitted resume tools without a generic outage or hidden workspace data when disabled", async () => {
  state.get.mockResolvedValue(ownedDashboardFixture(ownedBootstrapFixture({ features: { career_workspace: false } })));
  render(tree());
  await screen.findByText("Career Workspace is not enabled for this account yet. Your resume and profile tools remain available.");
  expect(screen.queryByText("Owned role")).toBeNull();
  expect(screen.queryByText("Owned follow-up")).toBeNull();
  expect(screen.queryByRole("button", { name: "Refresh dashboard" })).toBeNull();
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
});

it.each([401, 403, 429, 503])("does not fan out or retry after current snapshot HTTP %s", async (status) => {
  state.get.mockRejectedValue(new ApiError("Unavailable", status, {})); render(tree());
  await screen.findByRole("button", { name: "Refresh dashboard" });
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
  expect(screen.queryByText("Owned role")).toBeNull();
});

it.each([{ schema_version: "unknown" }, { summary: null }, { candidate_lifetime: { private: "synthetic" } }])("never invokes compatibility reads for a malformed current snapshot %j", async (patch) => {
  state.get.mockResolvedValue({ ...ownedDashboardFixture(), ...patch }); render(tree());
  await screen.findByRole("button", { name: "Refresh dashboard" });
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
});

it("uses only the existing reads after an exact old-server404 and strips full legacy rows to fixed previews", async () => {
  const snapshot = ownedDashboardFixture();
  state.get.mockImplementation(async (path: string) => {
    if (path === "/auth/dashboard") throw new ApiError("Absent", 404, {});
    if (path === "/auth/bootstrap") return snapshot.bootstrap;
    if (path === "/analytics/summary") return { ...snapshot.summary, match_history: [{ private: "Not projected" }] };
    if (path === "/v1/opportunities?limit=8") return { total: 1, items: snapshot.opportunities!.items.map((row) => ({ ...row, job_description: "Not projected" })) };
    if (path === "/v1/reminders?status=scheduled") return snapshot.reminders;
    throw new Error("Unexpected fallback path");
  });
  render(tree()); await screen.findByText("Owned role");
  expect(state.get.mock.calls.map(([path]) => path).sort()).toEqual([
    "/auth/dashboard", "/auth/bootstrap", "/analytics/summary", "/v1/opportunities?limit=8", "/v1/reminders?status=scheduled",
  ].sort());
  expect(JSON.stringify(client.getQueryData(ownedDashboardKey))).not.toContain("Not projected");
});

it("withholds a previously displayed snapshot after a current authority refusal", async () => {
  render(tree()); await screen.findByText("Owned role");
  state.get.mockRejectedValue(new ApiError("Not authenticated", 401, {}));
  await act(async () => { await client.invalidateQueries({ queryKey: ownedDashboardKey }); });
  await screen.findByRole("button", { name: "Refresh dashboard" });
  expect(screen.queryByText("Owned role")).toBeNull();
});

it("keeps the optional offer catalog separate and requests it only from known current credit data", async () => {
  state.get.mockImplementation(async (path: string) => path === "/auth/dashboard"
    ? ownedDashboardFixture(ownedBootstrapFixture({ availability: { catalog_enabled: true }, wallet: {
      schema_version: "unified-credits-v1", server_time: "2026-10-30T00:00:00Z", available_credits: 0, reserved_credits: 0, debt_credits: 0, lots: [],
    } })) : { checkout_enabled: false, products: [] });
  render(tree()); await screen.findByText("Owned role");
  await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2));
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/v1/credits/catalog"]);
});
