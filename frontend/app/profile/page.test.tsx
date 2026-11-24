import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, useQueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  get: vi.fn(), put: vi.fn(), post: vi.fn(), download: vi.fn(), track: vi.fn(), signOut: vi.fn(),
  auth: { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } } as {
    status: "loading" | "authenticated" | "unauthenticated";
    data: { user: { id: string; email: string } } | null;
  },
}));
vi.mock("../../lib/api", async (original) => ({ ...await original<typeof import("../../lib/api")>(),
  apiGet: state.get, apiPutJson: state.put, apiPostJson: state.post, apiDownload: state.download }));
vi.mock("next-auth/react", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children,
  useSession: () => state.auth }));
vi.mock("next/navigation", () => ({ usePathname: () => "/profile" }));
vi.mock("../../components/CareerMemoryPanel", () => ({ default: () => <p>Independent Career Memory</p> }));
vi.mock("../../components/WeeklyDigestPreference", () => ({ default: () => null }));
vi.mock("../../lib/analytics", () => ({ trackEvent: state.track, resetAnalyticsIdentity: vi.fn() }));
vi.mock("../../lib/candidateAuthClient", () => ({ guardedSignOut: state.signOut }));

import ProfilePage from "./page";
import SessionProviderWrapper from "../../components/SessionProviderWrapper";
import { ownedBootstrapFixture } from "../../lib/fixtures/ownedBootstrap";
import type { UserProfile } from "../../lib/types";

const clients = new Set<QueryClient>();
function ObserveClient() { clients.add(useQueryClient()); return null; }
function tree() { return <SessionProviderWrapper><ObserveClient /><ProfilePage /></SessionProviderWrapper>; }
function profile(name = "Saved candidate", email = "a@example.test"): UserProfile {
  return ownedBootstrapFixture({ profile: { full_name: name, email, headline: "Saved headline",
    bio: "Saved career summary", skills: ["Python"], tier: "premium", profile_completeness: 75 } }).profile!;
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const reads = () => state.get.mock.calls.filter(([path]) => path === "/auth/bootstrap");
beforeEach(() => {
  vi.resetAllMocks();
  state.auth = { status: "authenticated", data: { user: { id: "owner-a", email: "a@example.test" } } };
  state.get.mockImplementation((path: string) => path === "/auth/bootstrap"
    ? Promise.resolve(ownedBootstrapFixture({ profile: profile() })) : Promise.reject(new Error("Unexpected read")));
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("saved Profile read recovery", () => {
  it("blocks a blank editor after a refused read while retaining independent account controls", async () => {
    state.get.mockRejectedValue(new Error("Request capacity is temporarily allocated"));
    render(tree());
    expect(await screen.findByRole("heading", { name: "Profile unavailable" })).toBeVisible();
    expect(screen.queryByLabelText("Full name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save profile" })).toBeNull();
    expect(screen.queryByText("Free access is active.")).toBeNull();
    expect(screen.getByText(/Your access status could not be verified/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Export JSON" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Delete account" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "View credit packs" })).toHaveAttribute("href", "/billing");
    expect(reads()).toHaveLength(1);
    expect(state.put).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Export JSON" }));
    await waitFor(() => expect(state.download).toHaveBeenCalledOnce());
    expect(state.download).toHaveBeenCalledWith("/auth/export-account", "hirewiz-account-export.json", undefined, expect.any(AbortSignal));
  });

  it("makes one fresh read for explicit retry and exposes only the recovered saved values", async () => {
    const retry = deferred<ReturnType<typeof ownedBootstrapFixture>>();
    state.get.mockRejectedValueOnce(new Error("Unavailable")).mockReturnValueOnce(retry.promise);
    render(tree());
    const button = await screen.findByRole("button", { name: "Retry profile" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(reads()).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Loading profile…" })).toBeDisabled();
    expect(screen.queryByLabelText("Full name")).toBeNull();
    expect(screen.getByRole("button", { name: "Export JSON" })).toBeEnabled();
    await act(async () => retry.resolve(ownedBootstrapFixture({ profile: profile() })));
    expect(await screen.findByLabelText("Full name")).toHaveValue("Saved candidate");
    expect(screen.getByLabelText("Career summary")).toHaveValue("Saved career summary");
    expect(screen.getByRole("textbox", { name: /^Skills/ })).toHaveValue("Python");
    expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled();
    expect(reads()).toHaveLength(2); expect(state.put).not.toHaveBeenCalled();
  });

  it("keeps editing closed after another failed retry without an automatic retry loop", async () => {
    state.get.mockRejectedValue(new Error("Unavailable"));
    render(tree());
    fireEvent.click(await screen.findByRole("button", { name: "Retry profile" }));
    expect(await screen.findByRole("button", { name: "Retry profile" })).toBeEnabled();
    expect(reads()).toHaveLength(2); expect(screen.queryByLabelText("Full name")).toBeNull();
    expect(state.put).not.toHaveBeenCalled();
  });

  it("ignores a previous owner's late read after the real owner boundary remount", async () => {
    const previous = deferred<ReturnType<typeof ownedBootstrapFixture>>();
    state.get.mockReturnValueOnce(previous.promise).mockResolvedValueOnce(ownedBootstrapFixture({ profile: profile("Owner B", "b@example.test") }));
    const view = render(tree());
    await waitFor(() => expect(reads()).toHaveLength(1));
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    expect(await screen.findByLabelText("Full name")).toHaveValue("Owner B");
    await act(async () => previous.resolve(ownedBootstrapFixture({ profile: profile("Late owner A") })));
    expect(screen.getByLabelText("Full name")).toHaveValue("Owner B");
    expect(screen.queryByDisplayValue("Late owner A")).toBeNull();
    expect(state.put).not.toHaveBeenCalled();
  });

  it("preserves the user's draft after a save failure and never repeats the write automatically", async () => {
    state.put.mockRejectedValue(new Error("Save temporarily unavailable"));
    render(tree());
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "My unsaved name" } });
    fireEvent.change(screen.getByLabelText("Career summary"), { target: { value: "My unsaved summary" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Save temporarily unavailable");
    expect(screen.getByLabelText("Full name")).toHaveValue("My unsaved name");
    expect(screen.getByLabelText("Career summary")).toHaveValue("My unsaved summary");
    expect(state.put).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled();
  });

  it("does not replace newer edits with an older successful save response", async () => {
    const saved = deferred<UserProfile>(); state.put.mockReturnValue(saved.promise);
    render(tree());
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Submitted draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Newer unsaved draft" } });
    await act(async () => saved.resolve(profile("Submitted draft")));
    expect(screen.getByLabelText("Full name")).toHaveValue("Newer unsaved draft");
    expect(screen.queryByText("Saved", { exact: true })).toBeNull();
    expect(state.put).toHaveBeenCalledOnce();
    expect(state.put).toHaveBeenCalledWith("/auth/profile", expect.objectContaining({ full_name: "Submitted draft" }));
  });

  it("ignores a late previous-owner save without updating the current owner's form or cache", async () => {
    const saved = deferred<UserProfile>(); state.put.mockReturnValue(saved.promise);
    const view = render(tree());
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Owner A draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    state.get.mockResolvedValueOnce(ownedBootstrapFixture({ profile: profile("Owner B", "b@example.test") }));
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Owner B draft" } });
    await act(async () => saved.resolve(profile("Owner A saved")));
    expect(screen.getByLabelText("Full name")).toHaveValue("Owner B draft");
    expect(screen.queryByText("a@example.test")).toBeNull();
    expect(state.track).not.toHaveBeenCalledWith("profile_saved", expect.anything());
    expect(state.put).toHaveBeenCalledOnce();
  });

  it("preserves profile edits when an independent entitlement refresh fails", async () => {
    state.post.mockResolvedValue({});
    render(tree());
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Keep this draft" } });
    state.get.mockRejectedValueOnce(new Error("Refresh temporarily unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "End access" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Refresh temporarily unavailable");
    expect(screen.getByLabelText("Full name")).toHaveValue("Keep this draft");
    expect(state.post).toHaveBeenCalledOnce(); expect(state.put).not.toHaveBeenCalled();
  });

  it("does not sign out a new owner when a previous owner's deletion completes late", async () => {
    const deletion = deferred<unknown>(); state.post.mockReturnValue(deletion.promise);
    const view = render(tree());
    await screen.findByLabelText("Full name");
    fireEvent.click(screen.getByRole("button", { name: "Delete account" }));
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete" }));
    state.get.mockResolvedValueOnce(ownedBootstrapFixture({ profile: profile("Owner B", "b@example.test") }));
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Owner B draft" } });
    await act(async () => deletion.resolve({}));
    expect(state.post).toHaveBeenCalledWith("/auth/delete-account", {});
    expect(state.post).toHaveBeenCalledOnce(); expect(state.signOut).not.toHaveBeenCalled();
    expect(state.track).not.toHaveBeenCalledWith("account_deleted");
    expect(screen.getByLabelText("Full name")).toHaveValue("Owner B draft");
  });

  it("does not refresh entitlements after the requesting owner unmounts", async () => {
    const ended = deferred<unknown>(); state.post.mockReturnValue(ended.promise);
    const dispatch = vi.spyOn(window, "dispatchEvent");
    const view = render(tree());
    await screen.findByLabelText("Full name");
    fireEvent.click(screen.getByRole("button", { name: "End access" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    state.get.mockResolvedValueOnce(ownedBootstrapFixture({ profile: profile("Owner B", "b@example.test") }));
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    await screen.findByDisplayValue("Owner B");
    await act(async () => ended.resolve({}));
    expect(reads()).toHaveLength(2);
    expect(dispatch.mock.calls.some(([event]) => event.type === "refresh_analysis_units")).toBe(false);
    expect(state.track).not.toHaveBeenCalledWith("premium_access_ended");
  });

  it("aborts the actual export download before an old owner's late response can publish a file", async () => {
    const api = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
    state.download.mockImplementation(api.apiDownload);
    const response = deferred<Response>(), create = vi.fn(), revoke = vi.fn();
    vi.stubGlobal("fetch", vi.fn(() => response.promise));
    vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const view = render(tree());
    await screen.findByLabelText("Full name");
    fireEvent.click(screen.getByRole("button", { name: "Export JSON" }));
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal as AbortSignal;
    expect(signal.aborted).toBe(false);
    state.get.mockResolvedValueOnce(ownedBootstrapFixture({ profile: profile("Owner B", "b@example.test") }));
    state.auth = { status: "authenticated", data: { user: { id: "owner-b", email: "b@example.test" } } };
    view.rerender(tree());
    await screen.findByDisplayValue("Owner B");
    expect(signal.aborted).toBe(true);
    await act(async () => response.resolve(new Response('{"synthetic":"owner-a"}', { headers: { "content-type": "application/json" } })));
    expect(create).not.toHaveBeenCalled(); expect(click).not.toHaveBeenCalled();
    expect(state.track).not.toHaveBeenCalledWith("account_exported");
  });
});
