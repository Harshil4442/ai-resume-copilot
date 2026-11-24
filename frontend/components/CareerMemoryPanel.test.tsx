import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, useQueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), remove: vi.fn(),
  auth: { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } } as {
    status: "authenticated" | "loading" | "unauthenticated";
    data: { user: { id: string; email: string } } | null;
  },
}));
vi.mock("../lib/api", async (original) => ({ ...await original<typeof import("../lib/api")>(),
  apiGet: state.get, apiPutJson: state.put, apiDelete: state.remove }));
vi.mock("next-auth/react", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children,
  useSession: () => state.auth }));
vi.mock("next/navigation", () => ({ usePathname: () => "/profile" }));
vi.mock("./WeeklyDigestPreference", () => ({ default: () => null }));

import CareerMemoryPanel from "./CareerMemoryPanel";
import ProfilePage from "../app/profile/page";
import SessionProviderWrapper from "./SessionProviderWrapper";
import type { CareerMemory } from "../lib/career";

const clients = new Set<QueryClient>();
function ObserveClient() { clients.add(useQueryClient()); return null; }
function tree(profile = false) { return <SessionProviderWrapper><ObserveClient />{profile ? <ProfilePage /> : <CareerMemoryPanel />}</SessionProviderWrapper>; }
function row(value = "Software engineer"): CareerMemory {
  return { id: "mem_saved", category: "preferences", memory_key: "target.role", value, approval_state: "approved",
    provenance: "user", source_ref: null, created_at: "2026-11-25T00:00:00Z", updated_at: "2026-11-25T00:00:00Z" };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
beforeEach(() => { vi.resetAllMocks(); state.auth = { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } }; });
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.clear(); });

describe("verified Career Memory recovery", () => {
  it("does not describe a failed read as empty or allow edits and writes before explicit retry", async () => {
    state.get.mockRejectedValue(new Error("Unavailable"));
    render(tree());
    expect(screen.getByLabelText("Value")).toBeDisabled();
    expect(await screen.findByRole("alert")).toHaveTextContent("Career Memory is unavailable");
    expect(screen.queryByText("No reusable career memory has been approved.")).toBeNull();
    expect(screen.queryByText(/Only entries shown here/)).toBeNull();
    expect(screen.getByLabelText("Value")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(state.get).toHaveBeenCalledOnce(); expect(state.put).not.toHaveBeenCalled(); expect(state.remove).not.toHaveBeenCalled();
  });

  it("retries one read explicitly and restores the actual saved facts", async () => {
    const retry = deferred<CareerMemory[]>();
    state.get.mockRejectedValueOnce(new Error("Unavailable")).mockReturnValueOnce(retry.promise);
    render(tree());
    const button = await screen.findByRole("button", { name: "Retry Career Memory" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Value")).toBeDisabled();
    await act(async () => retry.resolve([row()]));
    expect(await screen.findByText("Software engineer")).toBeVisible();
    expect(screen.getByLabelText("Value")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Delete memory" })).toBeEnabled();
    expect(state.get.mock.calls.every(([, signal]) => signal instanceof AbortSignal)).toBe(true);
    expect(state.put).not.toHaveBeenCalled(); expect(state.remove).not.toHaveBeenCalled();
  });

  it("offers the empty state only after a successful empty read", async () => {
    state.get.mockResolvedValue([]); render(tree());
    expect(await screen.findByText("No reusable career memory has been approved.")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "My first approved preference" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([null, {}, [null], [{ id: "mem_missing", memory_key: "target.role" }], [{ id: 4, memory_key: "target.role", value: "bad" }]])("treats malformed HTTP200 data as unavailable", async (body) => {
    state.get.mockResolvedValue(body); render(tree());
    expect(await screen.findByRole("alert")).toHaveTextContent("Career Memory is unavailable");
    expect(screen.queryByText("No reusable career memory has been approved.")).toBeNull();
    expect(screen.getByLabelText("Value")).toBeDisabled();
    expect(state.get).toHaveBeenCalledOnce(); expect(state.put).not.toHaveBeenCalled();
  });

  it("keeps a newer draft through an earlier save and a failed refresh, then restores editing after retry", async () => {
    const saved = deferred<CareerMemory>();
    state.get.mockResolvedValueOnce([row()]).mockRejectedValueOnce(new Error("Refresh unavailable")).mockResolvedValueOnce([row("Submitted preference")]);
    state.put.mockReturnValue(saved.promise); render(tree());
    await screen.findByText("Software engineer");
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "Submitted preference" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "Keep this newer draft" } });
    await act(async () => saved.resolve(row("Submitted preference")));
    expect(await screen.findByRole("alert")).toHaveTextContent("Career Memory is unavailable");
    expect(screen.getByLabelText("Value")).toHaveValue("Keep this newer draft");
    expect(screen.getByLabelText("Value")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry Career Memory" }));
    await screen.findByText("Submitted preference");
    expect(screen.getByLabelText("Value")).toHaveValue("Keep this newer draft");
    expect(screen.getByLabelText("Value")).toBeEnabled();
    expect(state.put).toHaveBeenCalledOnce();
    expect(state.put).toHaveBeenCalledWith("/v1/career-memory", { category: "preferences", memory_key: "target.role", value: "Submitted preference" });
  });

  it("keeps the submitted draft when its post-save verification read fails", async () => {
    state.get.mockResolvedValueOnce([row()]).mockRejectedValueOnce(new Error("Refresh unavailable"));
    state.put.mockResolvedValue(row("Keep until verified")); render(tree());
    await screen.findByText("Software engineer");
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "Keep until verified" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Career Memory is unavailable");
    expect(screen.getByLabelText("Value")).toHaveValue("Keep until verified");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(state.put).toHaveBeenCalledOnce();
  });

  it("rejects late previous-owner reads and removes the old owner's draft before the next owner renders", async () => {
    const oldRead = deferred<CareerMemory[]>();
    state.get.mockReturnValueOnce(oldRead.promise).mockResolvedValueOnce([row("Owner B fact")]);
    const view = render(tree());
    await waitFor(() => expect(state.get).toHaveBeenCalledOnce());
    const oldSignal = state.get.mock.calls[0][1] as AbortSignal;
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree()); await screen.findByText("Owner B fact");
    expect(oldSignal.aborted).toBe(true);
    await act(async () => oldRead.resolve([row("Late owner A fact")]));
    expect(screen.queryByText("Late owner A fact")).toBeNull();
    expect(screen.getByLabelText("Value")).toHaveValue("");
  });

  it("ignores an old owner's late save without clearing or refreshing a new owner's draft", async () => {
    const saved = deferred<CareerMemory>();
    state.get.mockResolvedValueOnce([row("Owner A fact")]).mockResolvedValueOnce([row("Owner B fact")]);
    state.put.mockReturnValue(saved.promise); const view = render(tree());
    await screen.findByText("Owner A fact");
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "Owner A submitted" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree()); await screen.findByText("Owner B fact");
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "Owner B draft" } });
    await act(async () => saved.resolve(row("Owner A submitted")));
    expect(screen.getByLabelText("Value")).toHaveValue("Owner B draft");
    expect(state.get).toHaveBeenCalledTimes(2); expect(state.put).toHaveBeenCalledOnce();
  });

  it("stays independent when Profile is unavailable but its own read succeeds", async () => {
    state.get.mockImplementation((path: string) => path === "/v1/career-memory"
      ? Promise.resolve([row()]) : Promise.reject(new Error("Profile unavailable")));
    render(tree(true));
    await screen.findByRole("heading", { name: "Profile unavailable" });
    await screen.findByText("Software engineer");
    expect(screen.queryByRole("button", { name: "Save profile" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Value"), { target: { value: "An independent draft" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(state.get.mock.calls.filter(([path]) => path === "/v1/career-memory")).toHaveLength(1);
  });
});
