import React, { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  pathname: "/employer-jobs",
  session: { data: null, status: "loading" } as {
    data: { user: { id?: string; email?: string } } | null;
    status: "loading" | "authenticated" | "unauthenticated";
  },
}));

vi.mock("next-auth/react", () => ({
  SessionProvider: ({ children }: { children: React.ReactNode }) => children,
  useSession: () => auth.session,
}));
vi.mock("next/navigation", () => ({ usePathname: () => auth.pathname }));

import SessionProviderWrapper from "./SessionProviderWrapper";

const privateKeys = [["resumes"], ["feature-decisions"], ["employer-jobs", "catalog"]];
let clients: QueryClient[];
let readResume: () => Promise<string>;

// Query keys and local search/package drafts intentionally have no owner in their
// own keys. The shared provider must isolate all of them before the next render.
function PrivateWorkspace() {
  const client = useQueryClient();
  if (!clients.includes(client)) clients.push(client);
  const resume = useQuery({ queryKey: ["resumes"], queryFn: () => readResume() });
  const [activeSearch, setActiveSearch] = useState("");
  const [packageDigest, setPackageDigest] = useState("");
  return <>
    <p data-testid="resume">{resume.data ?? "Loading resume"}</p>
    <p data-testid="active-search">{activeSearch || "No active search"}</p>
    <p data-testid="package">{packageDigest || "No approval package"}</p>
    <button onClick={() => {
      privateKeys.forEach((key) => client.setQueryData(key, "Owner A private data"));
      setActiveSearch("Owner A search");
      setPackageDigest("Owner A approval digest");
    }}>Keep private draft</button>
  </>;
}

beforeEach(() => {
  clients = [];
  readResume = () => Promise.resolve("Owner A resume");
  auth.pathname = "/employer-jobs";
  auth.session = { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } };
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
});

describe("session owner boundary", () => {
  it("replaces owner caches and local search/approval state before a direct account swap renders", async () => {
    const view = render(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
    await screen.findByText("Owner A resume");
    fireEvent.click(screen.getByRole("button", { name: "Keep private draft" }));
    expect(screen.getByTestId("active-search")).toHaveTextContent("Owner A search");
    expect(screen.getByTestId("package")).toHaveTextContent("Owner A approval digest");
    const first = clients[0];
    let resolveResume!: (value: string) => void;
    readResume = () => new Promise<string>((resolve) => { resolveResume = resolve; });
    auth.session = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };

    view.rerender(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);

    const second = clients.at(-1)!;
    expect(second).not.toBe(first);
    privateKeys.forEach((key) => expect(second.getQueryData(key)).toBeUndefined());
    expect(screen.queryByText(/Owner A/)).not.toBeInTheDocument();
    expect(screen.getByTestId("active-search")).toHaveTextContent("No active search");
    expect(screen.getByTestId("package")).toHaveTextContent("No approval package");
    await act(async () => { resolveResume("Owner B resume"); });
    expect(await screen.findByText("Owner B resume")).toBeInTheDocument();

    // A late response or invalidation from the old owner cannot populate this cache.
    act(() => { first.setQueryData(["resumes"], "Late owner A response"); });
    expect(screen.getByTestId("resume")).toHaveTextContent("Owner B resume");
  });

  it("isolates loading, logout, and a second login while retaining same-owner state", async () => {
    const view = render(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
    await screen.findByText("Owner A resume");
    fireEvent.click(screen.getByRole("button", { name: "Keep private draft" }));
    view.rerender(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
    expect(clients).toHaveLength(1);
    expect(screen.getByTestId("package")).toHaveTextContent("Owner A approval digest");

    readResume = () => new Promise<string>(() => {});
    for (const status of ["loading", "unauthenticated"] as const) {
      auth.session = { status, data: null };
      view.rerender(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
      expect(screen.queryByText(/Owner A/)).not.toBeInTheDocument();
      expect(screen.queryByTestId("package")).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toBeInTheDocument();
    }
    auth.session = { status: "authenticated", data: { user: { id: "owner-b" } } };
    view.rerender(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
    expect(clients).toHaveLength(2);
    privateKeys.forEach((key) => expect(clients.at(-1)!.getQueryData(key)).toBeUndefined());
    expect(screen.getByTestId("active-search")).toHaveTextContent("No active search");
  });

  it("waits for an owner before mounting private queries or editable drafts but renders public pages immediately", () => {
    auth.session = { status: "loading", data: null };
    const view = render(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading your workspace");
    expect(clients).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Keep private draft" })).not.toBeInTheDocument();
    auth.pathname = "/";
    view.rerender(<SessionProviderWrapper><h1>Public home content</h1></SessionProviderWrapper>);
    expect(screen.getByRole("heading", { name: "Public home content" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not render private children for an authenticated session with no owner identity", async () => {
    auth.session = { status: "authenticated", data: { user: {} } };
    render(<SessionProviderWrapper><PrivateWorkspace /></SessionProviderWrapper>);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Your session could not be identified"));
    expect(clients).toHaveLength(0);
    expect(screen.queryByTestId("package")).not.toBeInTheDocument();
  });
});
