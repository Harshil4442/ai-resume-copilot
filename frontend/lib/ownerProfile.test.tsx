import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  pathname: "/dashboard",
  auth: { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } } as {
    status: "loading" | "authenticated" | "unauthenticated";
    data: { user: { id: string; email: string } } | null;
  },
}));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), apiGet: state.get }));
vi.mock("next-auth/react", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children, useSession: () => state.auth }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));

import Nav from "../components/Nav";
import SessionProviderWrapper from "../components/SessionProviderWrapper";
import { ownerProfileQueryOptions } from "./ownerProfile";
import type { UserProfile } from "./types";

const clients = new Set<QueryClient>();
function profile(email: string, units: number): UserProfile {
  return { email, ai_credits: units, tier: "free", skills: [], missing_fields: [], profile_completeness: 0 };
}
function Reader({ label }: { label: string }) {
  clients.add(useQueryClient());
  const query = useQuery(ownerProfileQueryOptions());
  return <p>{label}: {query.data ? `${query.data.email} / ${query.data.ai_credits}` : "Loading"}</p>;
}
function tree(extra = false) {
  return <SessionProviderWrapper><Nav /><Reader label="Dashboard" />{extra ? <Reader label="Later consumer" /> : null}</SessionProviderWrapper>;
}
const reads = () => state.get.mock.calls.filter(([path]) => path === "/auth/profile");

beforeEach(() => {
  state.pathname = "/dashboard";
  state.auth = { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } };
  state.get.mockReset();
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.clear(); });

describe("shared owner profile", () => {
  it("deduplicates Nav and page requests, retains fresh data for another consumer, and refetches once per credit event", async () => {
    let resolve!: (value: UserProfile) => void;
    const pending = new Promise<UserProfile>((done) => { resolve = done; });
    state.get.mockImplementation((path: string) => path === "/auth/profile"
      ? reads().length === 1 ? pending : Promise.resolve(profile("a@example.test", 9))
      : Promise.resolve({ features: {} }));
    const view = render(tree());
    await waitFor(() => expect(reads()).toHaveLength(1));
    expect(reads()[0][1]).toBeInstanceOf(AbortSignal);
    await act(async () => { resolve(profile("a@example.test", 7)); });
    await screen.findByText("Dashboard: a@example.test / 7");
    expect(screen.getByTitle("7 analysis units remaining")).toBeInTheDocument();
    view.rerender(tree(true));
    await screen.findByText("Later consumer: a@example.test / 7");
    expect(reads()).toHaveLength(1);
    act(() => { window.dispatchEvent(new Event("refresh_analysis_units")); });
    await screen.findByText("Dashboard: a@example.test / 9");
    expect(screen.getByTitle("9 analysis units remaining")).toBeInTheDocument();
    expect(reads()).toHaveLength(2);
  });

  it("isolates a direct owner switch and rejects a late previous-owner result", async () => {
    let oldReply!: (value: UserProfile) => void;
    const pending = new Promise<UserProfile>((done) => { oldReply = done; });
    state.get.mockImplementation((path: string) => path === "/auth/profile"
      ? reads().length === 1 ? pending : Promise.resolve(profile("b@example.test", 3))
      : Promise.resolve({ features: {} }));
    const view = render(tree());
    await waitFor(() => expect(reads()).toHaveLength(1));
    const oldSignal = reads()[0][1] as AbortSignal;
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    await screen.findByText("Dashboard: b@example.test / 3");
    expect(clients.size).toBe(2);
    expect(oldSignal.aborted).toBe(true);
    await act(async () => { oldReply(profile("a@example.test", 999)); });
    expect(screen.queryByText(/a@example.test/)).not.toBeInTheDocument();
    expect(screen.getByTitle("3 analysis units remaining")).toBeInTheDocument();
    expect(reads()).toHaveLength(2);
  });

  it("removes private profile views on logout/loading and uses a fresh cache for the next login", async () => {
    state.get.mockImplementation((path: string) => path === "/auth/profile"
      ? Promise.resolve(profile(state.auth.data!.user.email, 7)) : Promise.resolve({ features: {} }));
    const view = render(tree());
    await screen.findByText("Dashboard: a@example.test / 7");
    for (const status of ["loading", "unauthenticated"] as const) {
      state.auth = { status, data: null };
      view.rerender(tree());
      expect(screen.queryByText(/a@example.test/)).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toBeInTheDocument();
      expect(reads()).toHaveLength(1);
    }
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    await screen.findByText("Dashboard: b@example.test / 7");
    expect(reads()).toHaveLength(2);
  });

  it("does not request profile data for public anonymous navigation", () => {
    state.pathname = "/";
    state.auth = { status: "unauthenticated", data: null };
    render(<SessionProviderWrapper><Nav /></SessionProviderWrapper>);
    expect(state.get).not.toHaveBeenCalled();
  });
});
