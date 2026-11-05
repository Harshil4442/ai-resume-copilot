import React from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NotificationUnsubscribe from "./NotificationUnsubscribe";
import { clearNotificationUnsubscribeLink } from "../lib/notificationUnsubscribeLink";
import { NotificationRequestError, unsubscribeNotifications } from "../lib/notificationPreferencesClient";

vi.mock("../lib/notificationPreferencesClient", async (original) => ({ ...await original<typeof import("../lib/notificationPreferencesClient")>(), unsubscribeNotifications: vi.fn() }));
const token = "hwdu1.SYNTHETIC_PAYLOAD." + "T".repeat(43);
const off = { weekly_digest: false, generation: 2, policy_version: "weekly-v1", next_digest_at: null, projection_current: true };
beforeEach(() => { vi.resetAllMocks(); clearNotificationUnsubscribeLink(); history.replaceState(null, "", `/notifications/unsubscribe#token=${token}`); });
afterEach(() => { cleanup(); clearNotificationUnsubscribeLink(); history.replaceState(null, "", "/"); });

describe("login-free one-shot unsubscribe confirmation", () => {
  it("strips proof without sending on GET; only the explicit button posts it", async () => {
    vi.mocked(unsubscribeNotifications).mockResolvedValue(off);
    const view = render(<NotificationUnsubscribe />);
    expect(location.hash).toBe(""); expect(location.search).toBe("");
    expect(view.container.innerHTML).not.toContain(token);
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
    expect(unsubscribeNotifications).not.toHaveBeenCalled();
    fireEvent.click(view.getByRole("button", { name: "Stop weekly emails" }));
    expect(await view.findByRole("heading", { name: "Weekly emails turned off" })).toBeVisible();
    expect(unsubscribeNotifications).toHaveBeenCalledWith(token, expect.any(AbortSignal));
    expect(view.getByRole("status")).toHaveTextContent("already being sent");
    view.unmount(); render(<NotificationUnsubscribe />);
    expect(unsubscribeNotifications).toHaveBeenCalledOnce();
  });

  it("does not replay unknown outcomes on repeat click, rerender or remount", async () => {
    vi.mocked(unsubscribeNotifications).mockRejectedValue(new TypeError("network"));
    const view = render(<NotificationUnsubscribe />);
    const button = view.getByRole("button", { name: "Stop weekly emails" });
    fireEvent.click(button); fireEvent.click(button);
    expect(await view.findByRole("alert")).toHaveTextContent("No second request");
    expect(view.queryByRole("button")).toBeNull();
    view.rerender(<NotificationUnsubscribe />); view.unmount();
    const again = render(<NotificationUnsubscribe />);
    expect(again.getByRole("alert")).toHaveTextContent("could not confirm");
    expect(unsubscribeNotifications).toHaveBeenCalledOnce();
  });

  it("requires a new manual next step after a rejected or expired proof", async () => {
    vi.mocked(unsubscribeNotifications).mockRejectedValue(new NotificationRequestError(403));
    const view = render(<NotificationUnsubscribe />);
    fireEvent.click(view.getByRole("button", { name: "Stop weekly emails" }));
    expect(await view.findByRole("alert")).toHaveTextContent("invalid or no longer current");
    expect(view.getByRole("link", { name: "Open Profile" })).toHaveAttribute("href", "/profile");
    expect(view.queryByRole("button")).toBeNull();
  });

  it("clears the pending proof before an unmounted request can complete", async () => {
    let resolve!: (value: typeof off) => void;
    vi.mocked(unsubscribeNotifications).mockReturnValue(new Promise((yes) => { resolve = yes; }));
    const view = render(<NotificationUnsubscribe />);
    fireEvent.click(view.getByRole("button", { name: "Stop weekly emails" }));
    const signal = vi.mocked(unsubscribeNotifications).mock.calls[0][1];
    view.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => resolve(off));
    const again = render(<NotificationUnsubscribe />);
    expect(again.getByRole("alert")).toHaveTextContent("could not confirm");
    expect(unsubscribeNotifications).toHaveBeenCalledOnce();
  });

  it("does not send an absent proof or show its URL contents", () => {
    history.replaceState(null, "", "/notifications/unsubscribe?token=private#token=other");
    const view = render(<NotificationUnsubscribe />);
    expect(view.getByRole("alert")).toHaveTextContent("invalid");
    expect(view.queryByRole("button")).toBeNull();
    expect(unsubscribeNotifications).not.toHaveBeenCalled();
    expect(view.container.innerHTML).not.toContain("token=");
  });

  it("rebinds and strips a new same-path link before any manual submission", async () => {
    vi.mocked(unsubscribeNotifications).mockResolvedValue(off);
    const next = "hwdu1.SECOND_PAYLOAD." + "N".repeat(43);
    const view = render(<NotificationUnsubscribe />);
    history.replaceState(null, "", `/notifications/unsubscribe#token=${next}`);
    fireEvent(window, new HashChangeEvent("hashchange"));
    expect(location.hash).toBe("");
    expect(unsubscribeNotifications).not.toHaveBeenCalled();
    expect(view.container.innerHTML).not.toContain(next);
    fireEvent.click(view.getByRole("button", { name: "Stop weekly emails" }));
    await view.findByRole("heading", { name: "Weekly emails turned off" });
    expect(unsubscribeNotifications).toHaveBeenCalledWith(next, expect.any(AbortSignal));
    expect(unsubscribeNotifications).toHaveBeenCalledOnce();
  });

  it("rejects an old in-flight completion after same-path link replacement", async () => {
    let resolve!: (value: typeof off) => void;
    vi.mocked(unsubscribeNotifications).mockReturnValueOnce(new Promise((yes) => { resolve = yes; })).mockResolvedValueOnce(off);
    const next = "hwdu1.SECOND_PAYLOAD." + "N".repeat(43);
    const view = render(<NotificationUnsubscribe />);
    fireEvent.click(view.getByRole("button", { name: "Stop weekly emails" }));
    const oldSignal = vi.mocked(unsubscribeNotifications).mock.calls[0][1];
    history.replaceState(null, "", `/notifications/unsubscribe#token=${next}`);
    fireEvent(window, new HashChangeEvent("hashchange"));
    expect(oldSignal.aborted).toBe(true); expect(location.hash).toBe("");
    await act(async () => resolve(off));
    expect(view.getByRole("button", { name: "Stop weekly emails" })).toBeEnabled();
    expect(view.queryByRole("heading", { name: "Weekly emails turned off" })).toBeNull();
    expect(unsubscribeNotifications).toHaveBeenCalledOnce();
    fireEvent.click(view.getByRole("button", { name: "Stop weekly emails" }));
    await view.findByRole("heading", { name: "Weekly emails turned off" });
    expect(unsubscribeNotifications).toHaveBeenLastCalledWith(next, expect.any(AbortSignal));
    expect(unsubscribeNotifications).toHaveBeenCalledTimes(2);
  });
});
