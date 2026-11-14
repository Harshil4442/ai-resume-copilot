import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ get: vi.fn(), owner: "owner-a", status: "authenticated", pathname: "/dashboard" }));
vi.mock("../../lib/api", async (original) => ({ ...await original<typeof import("../../lib/api")>(), apiGet: state.get }));
vi.mock("next-auth/react", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children,
  useSession: () => ({ status: state.status, data: state.status === "authenticated" ? { user: { id: state.owner } } : null }) }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
import Nav from "../../components/Nav";
import SessionProviderWrapper from "../../components/SessionProviderWrapper";
import { ApiError } from "../../lib/api";
import { ownedDashboardFixture } from "../../lib/fixtures/ownedDashboard";
import { ownedBootstrapFixture } from "../../lib/fixtures/ownedBootstrap";
import { ownedDashboardKey } from "../../lib/ownedDashboardClient";
import { fetchOwnedProfile, ownedBootstrapKey } from "../../lib/ownedBootstrapClient";
import Dashboard from "./page";
let client: QueryClient;
function tree() { return <QueryClientProvider client={client}><Nav /><Dashboard /></QueryClientProvider>; }
beforeEach(() => {
  state.owner = "owner-a"; state.status = "authenticated"; state.pathname = "/dashboard";
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
afterEach(() => { cleanup(); client.clear(); vi.restoreAllMocks(); });

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

it("reuses the verified Dashboard overview after navigation until an explicit refresh or invalidation", async () => {
  const view = render(tree());
  await screen.findByText("Owned role");
  state.pathname = "/resume";
  view.rerender(<QueryClientProvider client={client}><Nav /></QueryClientProvider>);
  expect(screen.getByTitle("7 analysis units remaining")).toBeVisible();
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
  state.get.mockResolvedValue(ownedBootstrapFixture({ profile: { ai_credits: 4 } }));
  await act(async () => { await fetchOwnedProfile(client, true); });
  await screen.findByTitle("4 analysis units remaining");
  state.get.mockResolvedValue(ownedBootstrapFixture({ profile: { ai_credits: 3 } }));
  await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
  await screen.findByTitle("3 analysis units remaining");
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/bootstrap", "/auth/bootstrap"]);
});

it("requests a fresh bootstrap when Dashboard's existing freshness window has elapsed", async () => {
  const now = vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_000);
  let finish!: (value: unknown) => void;
  state.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(tree());
  await waitFor(() => expect(finish).toBeDefined());
  now.mockReturnValue(1_800_000_001_000);
  await act(async () => { finish(ownedDashboardFixture()); });
  await screen.findByText("Owned role");
  // It is still younger than 30s if incorrectly timestamped at response time.
  now.mockReturnValue(1_800_000_030_001);
  state.get.mockResolvedValue(ownedBootstrapFixture({ profile: { ai_credits: 4 } }));
  state.pathname = "/workspace";
  view.rerender(<QueryClientProvider client={client}><Nav /></QueryClientProvider>);
  await screen.findByTitle("4 analysis units remaining");
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/bootstrap"]);
});

it("does not reuse a Dashboard projection for another owner or after signing out", async () => {
  state.get.mockImplementation(async (path: string) => {
    const bootstrap = ownedBootstrapFixture({ profile: { ai_credits: state.owner === "owner-a" ? 7 : 4 } });
    return path === "/auth/dashboard" ? ownedDashboardFixture(bootstrap) : bootstrap;
  });
  const ownedTree = () => <SessionProviderWrapper><Nav />{state.pathname === "/dashboard" ? <Dashboard /> : null}</SessionProviderWrapper>;
  const view = render(ownedTree());
  await screen.findByTitle("7 analysis units remaining");
  state.pathname = "/resume";
  state.owner = "owner-b";
  view.rerender(ownedTree());
  expect(screen.queryByTitle("7 analysis units remaining")).toBeNull();
  await screen.findByTitle("4 analysis units remaining");
  state.status = "unauthenticated";
  view.rerender(ownedTree());
  expect(screen.queryByTitle(/analysis units remaining/)).toBeNull();
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/bootstrap"]);
});

it("does not make an invalidated bootstrap fresh with a late dashboard response", async () => {
  let finish!: (value: unknown) => void;
  state.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(tree());
  await waitFor(() => expect(finish).toBeDefined());
  await act(async () => { await client.invalidateQueries({ queryKey: ownedBootstrapKey, exact: true }); });
  await act(async () => { finish(ownedDashboardFixture()); });
  await screen.findByText("Owned role");
  expect(client.getQueryState(ownedBootstrapKey)?.isInvalidated).toBe(true);
  expect(client.getQueryData(ownedBootstrapKey)).toBeUndefined();
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
});

it("never overwrites an in-flight or newer direct bootstrap with a dashboard projection", async () => {
  let finishDashboard!: (value: unknown) => void, finishBootstrap!: (value: unknown) => void;
  state.get.mockImplementation((path: string) => new Promise((resolve) => {
    if (path === "/auth/dashboard") finishDashboard = resolve;
    else finishBootstrap = resolve;
  }));
  render(tree());
  await waitFor(() => expect(finishDashboard).toBeDefined());
  let fresh!: ReturnType<typeof fetchOwnedProfile>;
  act(() => { fresh = fetchOwnedProfile(client, true); });
  await waitFor(() => expect(finishBootstrap).toBeDefined());
  await act(async () => { finishDashboard(ownedDashboardFixture()); });
  await screen.findByText("Owned role");
  expect(client.getQueryState(ownedBootstrapKey)?.fetchStatus).toBe("fetching");
  expect(client.getQueryData(ownedBootstrapKey)).toBeUndefined();
  const direct = ownedBootstrapFixture({ profile: { ai_credits: 4 } });
  await act(async () => { finishBootstrap(direct); await fresh; });
  expect(client.getQueryData(ownedBootstrapKey)).toEqual(direct);
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/bootstrap"]);
});

it("does not evict a direct bootstrap when the next Dashboard attempt is refused", async () => {
  render(tree());
  await screen.findByText("Owned role");
  const direct = ownedBootstrapFixture({ profile: { ai_credits: 4 } });
  state.get.mockResolvedValueOnce(direct);
  await act(async () => { await fetchOwnedProfile(client, true); });
  state.get.mockRejectedValueOnce(new ApiError("Not authenticated", 401, {}));
  await act(async () => { window.dispatchEvent(new Event("refresh_unified_credits")); });
  await screen.findByRole("button", { name: "Refresh dashboard" });
  expect(client.getQueryData(ownedBootstrapKey)).toEqual(direct);
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/bootstrap", "/auth/dashboard"]);
});

it("does not export a partially available overview into the bootstrap cache", async () => {
  state.get.mockResolvedValue(ownedDashboardFixture(ownedBootstrapFixture({ availability: null })));
  render(tree());
  await screen.findByText("Owned role");
  expect(client.getQueryData(ownedBootstrapKey)).toBeUndefined();
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
  expect(client.getQueryData(ownedBootstrapKey)).toEqual(ownedBootstrapFixture());
  state.get.mockRejectedValue(new ApiError("Not authenticated", 401, {}));
  await act(async () => { window.dispatchEvent(new Event("refresh_unified_credits")); });
  await screen.findByRole("button", { name: "Refresh dashboard" });
  expect(screen.queryByText("Owned role")).toBeNull();
  expect(client.getQueryData(ownedBootstrapKey)).toBeUndefined();
  expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/dashboard"]);
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
