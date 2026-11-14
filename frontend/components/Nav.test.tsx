import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  pathname: "/dashboard",
  authenticated: true,
  loading: false,
  owner: "owner-a",
}));
vi.mock("../lib/api", async (original) => ({ ...await original<typeof import("../lib/api")>(), apiGet: state.get }));
vi.mock("next-auth/react", () => ({
  SessionProvider: ({ children }: { children: ReactNode }) => children,
  useSession: () => ({ status: state.loading ? "loading" : state.authenticated ? "authenticated" : "unauthenticated",
    data: state.authenticated ? { user: { id: state.owner, email: `${state.owner}@example.test` } } : null }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));

import Nav from "./Nav";
import SessionProviderWrapper from "./SessionProviderWrapper";
import { ownedBootstrapFixture } from "../lib/fixtures/ownedBootstrap";
import { ownedDashboardFixture } from "../lib/fixtures/ownedDashboard";
import { ownedDashboardKey } from "../lib/ownedDashboardClient";
import { ownedBootstrapKey } from "../lib/ownedBootstrapClient";

const clients = new Set<QueryClient>();
const account = { ai_credits: 7, job_service_credits: 3 };
function createClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  clients.add(client);
  return client;
}
function tree(client: QueryClient) {
  return <QueryClientProvider client={client}><Nav /></QueryClientProvider>;
}

beforeEach(() => {
  state.pathname = "/dashboard";
  state.authenticated = true;
  state.loading = false;
  state.owner = "owner-a";
  state.get.mockReset();
  state.get.mockImplementation((path: string) => { const value = ownedBootstrapFixture({ profile: account, features: { career_workspace: false } }); return Promise.resolve(path === "/auth/dashboard" ? ownedDashboardFixture(value) : value); });
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.clear(); onlineManager.setOnline(true); });

describe("connection-page header request budget", () => {
  it("does not request account details on the exact companion route, including invalidation and reconnect", async () => {
    state.pathname = "/browser-companion";
    const client = createClient();
    render(tree(client));
    await act(async () => {
      window.dispatchEvent(new Event("refresh_analysis_units"));
      await client.invalidateQueries();
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    expect(state.get).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Jobs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Log out" })).toBeInTheDocument();
    expect(screen.queryByTitle(/analysis units remaining/)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
  });

  it("hides cached balances and does not invalidate shared profile/catalog data after navigating to companion", async () => {
    const client = createClient();
    client.setQueryData(ownedBootstrapKey, ownedBootstrapFixture({ profile: account, features: { career_workspace: false } }));
    client.setQueryData(ownedDashboardKey, ownedDashboardFixture(ownedBootstrapFixture({ profile: account, features: { career_workspace: false } })));
    client.setQueryData(["employer-jobs", "catalog"], { balance: 3 });
    const view = render(tree(client));
    expect(screen.getByTitle("7 analysis units remaining")).toBeInTheDocument();
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
    state.pathname = "/browser-companion";
    view.rerender(tree(client));
    await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    expect(state.get).not.toHaveBeenCalled();
    expect(client.getQueryState(ownedBootstrapKey)?.isInvalidated).toBe(false);
    expect(client.getQueryState(["employer-jobs", "catalog"])?.isInvalidated).toBe(false);
    expect(screen.queryByTitle(/analysis units remaining/)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Log out" })).toBeInTheDocument();
  });

  it("resumes normal account queries when leaving the companion route", async () => {
    state.pathname = "/browser-companion";
    const client = createClient();
    const view = render(tree(client));
    expect(state.get).not.toHaveBeenCalled();
    state.pathname = "/employer-jobs";
    view.rerender(tree(client));
    await screen.findByTitle("7 analysis units remaining");
    expect(state.get.mock.calls.map(([path]) => path).sort()).toEqual(["/auth/bootstrap"]);
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it.each(["/dashboard", "/employer-jobs", "/browser-companion/details", "/login/help", "/register/help", "/forgot-password/help", "/reset-password/help"])("keeps ordinary queries, credit refresh and feature filtering on %s", async (pathname) => {
    state.pathname = pathname;
    const client = createClient();
    render(tree(client));
    await screen.findByTitle("7 analysis units remaining");
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(1));
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
    await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    await waitFor(() => expect(state.get.mock.calls.filter(([path]) => path === (state.pathname === "/dashboard" ? "/auth/dashboard" : "/auth/bootstrap"))).toHaveLength(2));
    expect(state.get.mock.calls.some(([path]) => path === "/v1/features")).toBe(false);
  });

  it.each(["/login", "/register", "/forgot-password", "/reset-password"])("defers header reads during a session refresh on %s", async (pathname) => {
    state.pathname = pathname;
    state.authenticated = false;
    state.loading = true;
    const client = createClient();
    const view = render(tree(client));
    expect(screen.getByRole("link", { name: "Log in" })).toBeInTheDocument();
    state.loading = false;
    state.authenticated = true;
    view.rerender(tree(client));
    await act(async () => {
      window.dispatchEvent(new Event("refresh_analysis_units"));
      window.dispatchEvent(new Event("refresh_unified_credits"));
      await client.invalidateQueries();
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    expect(state.get).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Log out" })).toBeInTheDocument();
    expect(screen.queryByTitle(/analysis units remaining|job service credits|credits available/)).not.toBeInTheDocument();
  });

  it("starts one owned dashboard read after leaving the authenticated login page", async () => {
    state.pathname = "/login";
    const client = createClient();
    const view = render(tree(client));
    expect(state.get).not.toHaveBeenCalled();
    state.pathname = "/dashboard";
    view.rerender(tree(client));
    await screen.findByTitle("7 analysis units remaining");
    expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard"]);
    expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it.each([false, true])("hides cached balances on authentication pages when unified credits are %s", async (catalogEnabled) => {
    const client = createClient();
    const value = ownedBootstrapFixture({ profile: account, features: { career_workspace: false },
      availability: { catalog_enabled: catalogEnabled }, wallet: { schema_version: "unified-credits-v1", server_time: "2026-10-27T00:00:00Z",
        available_credits: 11, reserved_credits: 0, debt_credits: 0, lots: [] } });
    client.setQueryData(ownedBootstrapKey, value);
    client.setQueryData(ownedDashboardKey, ownedDashboardFixture(value));
    const view = render(tree(client));
    expect(screen.getByTitle(catalogEnabled ? "11 credits available" : "7 analysis units remaining")).toBeInTheDocument();
    state.pathname = "/login";
    view.rerender(tree(client));
    await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    expect(state.get).not.toHaveBeenCalled();
    expect(screen.queryByTitle(/analysis units remaining|job service credits|credits available/)).not.toBeInTheDocument();
    expect(screen.queryByText("View credits")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Log out" })).toBeInTheDocument();
  });

  it("retains the owner cache boundary while deferring login reads", async () => {
    state.pathname = "/login";
    const view = render(<SessionProviderWrapper><Nav /></SessionProviderWrapper>);
    state.owner = "owner-b";
    view.rerender(<SessionProviderWrapper><Nav /></SessionProviderWrapper>);
    expect(state.get).not.toHaveBeenCalled();
    state.pathname = "/dashboard";
    view.rerender(<SessionProviderWrapper><Nav /></SessionProviderWrapper>);
    await screen.findByTitle("7 analysis units remaining");
    expect(state.get).toHaveBeenCalledTimes(1);

    let finish: (value: unknown) => void = () => {};
    const nextOwner = new Promise((resolve) => { finish = resolve; });
    state.get.mockImplementationOnce(() => nextOwner);
    state.owner = "owner-c";
    view.rerender(<SessionProviderWrapper><Nav /></SessionProviderWrapper>);
    expect(screen.queryByTitle("7 analysis units remaining")).not.toBeInTheDocument();
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2));
    await act(async () => { finish(ownedDashboardFixture(ownedBootstrapFixture({ profile: { ai_credits: 4, job_service_credits: 2 } }))); });
    await screen.findByTitle("4 analysis units remaining");
    expect(screen.queryByTitle("7 analysis units remaining")).not.toBeInTheDocument();
    expect(state.get.mock.calls.map(([path]) => path)).toEqual(["/auth/dashboard", "/auth/dashboard"]);
  });

  it("keeps public anonymous navigation without private requests", async () => {
    state.pathname = "/";
    state.authenticated = false;
    render(tree(createClient()));
    await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    expect(state.get).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Log in" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Log out" })).not.toBeInTheDocument();
  });

  it("shows only the authoritative unified balance after rollout and refreshes it separately", async () => {
    let credits = 11;
    state.get.mockImplementation((path: string) => { const value = ownedBootstrapFixture({ profile: account,
      availability: { catalog_enabled: true }, wallet: { schema_version: "unified-credits-v1", server_time: "2026-10-27T00:00:00Z",
        available_credits: credits, reserved_credits: 0, debt_credits: 0, lots: [] } }); return Promise.resolve(path === "/auth/dashboard" ? ownedDashboardFixture(value) : value); });
    const client = createClient(); const view = render(tree(client));
    await screen.findByTitle("11 credits available");
    expect(screen.queryByTitle(/analysis units remaining/)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
    credits = 9; act(() => { window.dispatchEvent(new Event("refresh_unified_credits")); });
    await screen.findByTitle("9 credits available");
    expect(state.get.mock.calls.filter(([path]) => path === (state.pathname === "/dashboard" ? "/auth/dashboard" : "/auth/bootstrap"))).toHaveLength(2);
    state.pathname = "/browser-companion"; view.rerender(tree(client));
    expect(screen.queryByTitle(/credits available/)).not.toBeInTheDocument();
  });

  it("does not infer rollout from a wallet, a missing flag or a truthy string", async () => {
    state.get.mockImplementation((path: string) => { const value = ownedBootstrapFixture({ profile: account, availability: null,
      wallet: { schema_version: "unified-credits-v1", server_time: "2026-10-27T00:00:00Z", available_credits: 99, reserved_credits: 0, debt_credits: 0, lots: [] } }); return Promise.resolve(path === "/auth/dashboard" ? ownedDashboardFixture(value) : value); });
    render(tree(createClient())); await screen.findByTitle("7 analysis units remaining");
    expect(state.get.mock.calls.some(([path]) => path === "/v1/credits/wallet")).toBe(false);
  });
});
