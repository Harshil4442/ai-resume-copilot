import React from "react";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WeeklyDigestPreference from "./WeeklyDigestPreference";
import ProfilePage from "../app/profile/page";
import { changeNotificationPreference, readNotificationPreference } from "../lib/notificationPreferencesClient";
import { fetchOwnedProfile } from "../lib/ownedBootstrapClient";

const session = vi.hoisted(() => ({ status: "authenticated", data: { user: { id: "owner-1", email: "owner@example.invalid" } } }));
vi.mock("next-auth/react", () => ({ useSession: () => session }));
vi.mock("../lib/notificationPreferencesClient", () => ({ readNotificationPreference: vi.fn(), changeNotificationPreference: vi.fn() }));
vi.mock("../lib/ownedBootstrapClient", () => ({ fetchOwnedProfile: vi.fn(), updateOwnedProfile: vi.fn() }));
vi.mock("./CareerMemoryPanel", () => ({ default: () => null }));
const off = { weekly_digest: false, generation: 0, policy_version: "weekly-v1", next_digest_at: null, projection_current: true };
const on = { ...off, weekly_digest: true, generation: 1, next_digest_at: "2026-11-09T00:00:00+00:00" };

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
beforeEach(() => {
  vi.resetAllMocks(); session.status = "authenticated"; session.data = { user: { id: "owner-1", email: "owner@example.invalid" } };
  vi.mocked(readNotificationPreference).mockResolvedValue(off);
});
afterEach(cleanup);

describe("explicit owner-bound digest preference", () => {
  it("starts the preference read while the independent Profile read is still pending", async () => {
    vi.mocked(fetchOwnedProfile).mockReturnValue(new Promise(() => {}));
    const client = new QueryClient();
    const view = render(<QueryClientProvider client={client}><ProfilePage /></QueryClientProvider>);
    expect(await view.findByRole("checkbox")).not.toBeChecked();
    expect(fetchOwnedProfile).toHaveBeenCalledOnce();
    expect(readNotificationPreference).toHaveBeenCalledOnce();
    view.unmount(); client.clear();
  });
  it("loads read-only and sends the exact generation only after explicit change and save", async () => {
    vi.mocked(changeNotificationPreference).mockResolvedValue(on);
    const view = render(<WeeklyDigestPreference />);
    const choice = await view.findByRole("checkbox");
    expect(choice).not.toBeChecked();
    expect(changeNotificationPreference).not.toHaveBeenCalled();
    expect(view.getByRole("button", { name: "Turn off weekly emails" })).toBeDisabled();
    fireEvent.click(choice);
    expect(changeNotificationPreference).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("button", { name: "Enable weekly emails" }));
    expect(await view.findByText("Email preference saved.")).toBeVisible();
    expect(changeNotificationPreference).toHaveBeenCalledWith(off, true, expect.any(AbortSignal));
    expect(view.getByText("Weekly emails: On")).toBeVisible();
  });

  it("does not imply opt-in or off when the initial check is unavailable", async () => {
    vi.mocked(readNotificationPreference).mockRejectedValue(new Error("503 private details"));
    const view = render(<WeeklyDigestPreference />);
    expect(await view.findByRole("alert")).toHaveTextContent("could not be verified");
    expect(view.queryByRole("checkbox")).toBeNull();
    expect(view.queryByText(/Weekly emails: (On|Off)/)).toBeNull();
    expect(view.container).not.toHaveTextContent("private details");
    expect(readNotificationPreference).toHaveBeenCalledOnce();
    expect(changeNotificationPreference).not.toHaveBeenCalled();
  });

  it("never repeats an uncertain PUT; manual recovery is a new GET of current state", async () => {
    const pending = deferred<typeof on>();
    vi.mocked(changeNotificationPreference).mockReturnValue(pending.promise);
    const view = render(<WeeklyDigestPreference />);
    fireEvent.click(await view.findByRole("checkbox"));
    const button = view.getByRole("button", { name: "Enable weekly emails" });
    fireEvent.click(button); fireEvent.click(button);
    expect(changeNotificationPreference).toHaveBeenCalledOnce();
    await act(async () => pending.reject(new Error("timeout")));
    expect(await view.findByRole("alert")).toHaveTextContent("will not repeat");
    expect(view.queryByRole("checkbox")).toBeNull();
    vi.mocked(readNotificationPreference).mockResolvedValue(on);
    fireEvent.click(view.getByRole("button", { name: "Refresh current preference" }));
    expect(await view.findByRole("checkbox")).toBeChecked();
    expect(readNotificationPreference).toHaveBeenCalledTimes(2);
    expect(changeNotificationPreference).toHaveBeenCalledOnce();
  });

  it("aborts old owner reads and ignores their result after a direct owner change", async () => {
    const previous = deferred<typeof on>();
    vi.mocked(readNotificationPreference).mockReturnValueOnce(previous.promise).mockResolvedValueOnce(off);
    const view = render(<WeeklyDigestPreference />);
    const oldSignal = vi.mocked(readNotificationPreference).mock.calls[0][0];
    session.data = { user: { id: "owner-2", email: "other@example.invalid" } };
    view.rerender(<WeeklyDigestPreference />);
    expect(oldSignal.aborted).toBe(true);
    expect(await view.findByRole("checkbox")).not.toBeChecked();
    await act(async () => previous.resolve(on));
    expect(view.getByText("Weekly emails: Off")).toBeVisible();
    expect(view.queryByText("Weekly emails: On")).toBeNull();
  });

  it("does not let an old owner's PUT update the new owner's preference", async () => {
    const previous = deferred<typeof on>();
    vi.mocked(changeNotificationPreference).mockReturnValue(previous.promise);
    const view = render(<WeeklyDigestPreference />);
    fireEvent.click(await view.findByRole("checkbox"));
    fireEvent.click(view.getByRole("button", { name: "Enable weekly emails" }));
    const oldSignal = vi.mocked(changeNotificationPreference).mock.calls[0][2];
    session.data = { user: { id: "owner-2", email: "other@example.invalid" } };
    view.rerender(<WeeklyDigestPreference />);
    expect(oldSignal.aborted).toBe(true);
    await act(async () => previous.resolve(on));
    expect(await view.findByRole("checkbox")).not.toBeChecked();
    expect(view.queryByText("Email preference saved.")).toBeNull();
  });

  it("keeps settings unavailable while auth is loading and aborts on unmount", async () => {
    session.status = "loading";
    const view = render(<WeeklyDigestPreference />);
    expect(view.queryByRole("checkbox")).toBeNull();
    expect(readNotificationPreference).not.toHaveBeenCalled();
    session.status = "authenticated"; view.rerender(<WeeklyDigestPreference />);
    await waitFor(() => expect(readNotificationPreference).toHaveBeenCalledOnce());
    const signal = vi.mocked(readNotificationPreference).mock.calls[0][0];
    view.unmount(); expect(signal.aborted).toBe(true);
  });
});
