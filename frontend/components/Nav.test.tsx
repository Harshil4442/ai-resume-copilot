import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  pathname: "/dashboard",
  authenticated: true,
}));
vi.mock("../lib/api", () => ({ apiGet: state.get }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: state.authenticated ? "authenticated" : "unauthenticated" }) }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));

import Nav from "./Nav";

const clients = new Set<QueryClient>();
const account = { ai_credits: 7, job_service_credits: 3 };
const decisions = { features: { career_workspace: { enabled: false } } };
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
  state.get.mockReset();
  state.get.mockImplementation((path: string) => Promise.resolve(path === "/auth/profile" ? account : decisions));
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
    client.setQueryData(["profile"], account);
    client.setQueryData(["feature-decisions"], decisions);
    client.setQueryData(["unified-credits", "availability"], { catalog_enabled: false, execution_enabled: false, supported_operations: [] });
    client.setQueryData(["employer-jobs", "catalog"], { balance: 3 });
    const view = render(tree(client));
    expect(screen.getByTitle("7 analysis units remaining")).toBeInTheDocument();
    expect(screen.getByTitle("3 job service credits")).toBeInTheDocument();
    state.pathname = "/browser-companion";
    view.rerender(tree(client));
    await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    expect(state.get).not.toHaveBeenCalled();
    expect(client.getQueryState(["profile"])?.isInvalidated).toBe(false);
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
    expect(state.get.mock.calls.map(([path]) => path).sort()).toEqual(["/auth/profile", "/v1/credits/availability", "/v1/features"]);
    expect(screen.getByTitle("3 job service credits")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it.each(["/dashboard", "/employer-jobs", "/browser-companion/details"])("keeps ordinary queries, credit refresh and feature filtering on %s", async (pathname) => {
    state.pathname = pathname;
    const client = createClient();
    render(tree(client));
    await screen.findByTitle("7 analysis units remaining");
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(3));
    expect(screen.getByTitle("3 job service credits")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Workspace" })).not.toBeInTheDocument();
    await act(async () => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    await waitFor(() => expect(state.get.mock.calls.filter(([path]) => path === "/auth/profile")).toHaveLength(2));
    expect(state.get.mock.calls.filter(([path]) => path === "/v1/features")).toHaveLength(1);
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
    state.get.mockImplementation((path: string) => Promise.resolve(path === "/v1/credits/availability"
      ? { catalog_enabled: true, execution_enabled: false, supported_operations: [] }
      : path === "/v1/credits/wallet" ? { schema_version: "unified-credits-v1", server_time: "2026-10-27T00:00:00Z", available_credits: credits, reserved_credits: 0, debt_credits: 0, lots: [] }
        : path === "/auth/profile" ? account : decisions));
    const client = createClient(); const view = render(tree(client));
    await screen.findByTitle("11 credits available");
    expect(screen.queryByTitle(/analysis units remaining/)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/job service credits/)).not.toBeInTheDocument();
    credits = 9; act(() => { window.dispatchEvent(new Event("refresh_unified_credits")); });
    await screen.findByTitle("9 credits available");
    expect(state.get.mock.calls.filter(([path]) => path === "/auth/profile")).toHaveLength(1);
    state.pathname = "/browser-companion"; view.rerender(tree(client));
    expect(screen.queryByTitle(/credits available/)).not.toBeInTheDocument();
  });

  it("does not infer rollout from a wallet, a missing flag or a truthy string", async () => {
    state.get.mockImplementation((path: string) => Promise.resolve(path === "/v1/credits/availability"
      ? { catalog_enabled: "true", execution_enabled: true } : path === "/auth/profile" ? account : decisions));
    render(tree(createClient())); await screen.findByTitle("7 analysis units remaining");
    expect(state.get.mock.calls.some(([path]) => path === "/v1/credits/wallet")).toBe(false);
  });
});
