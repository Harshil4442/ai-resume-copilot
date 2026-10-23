import React, { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PasswordResetConfirm from "./PasswordResetConfirm";
import PasswordResetRequest from "./PasswordResetRequest";
import SessionProviderWrapper from "./SessionProviderWrapper";
import { capturePasswordResetLink, clearPasswordResetToken, readPasswordResetLink, readPasswordResetToken } from "../lib/passwordResetLink";

const auth = vi.hoisted(() => ({ csrf: vi.fn(), status: "loading" as "loading" | "unauthenticated", pathname: "/" }));
vi.mock("next-auth/react", () => ({ getCsrfToken: auth.csrf, useSession: () => ({ data: null, status: auth.status }),
  SessionProvider: ({ children }: { children: React.ReactNode }) => children }));
vi.mock("next/navigation", () => ({ usePathname: () => auth.pathname }));
const token = "T".repeat(43), password = "human-new-password-123";
beforeEach(() => {
  auth.csrf.mockReset().mockResolvedValue("c".repeat(64));
  clearPasswordResetToken();
  auth.status = "loading"; auth.pathname = "/";
  history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); history.replaceState(null, "", "/"); });
function confirm(strict = false) {
  history.replaceState(null, "", `/reset-password#token=${token}`);
  capturePasswordResetLink();
  return render(strict ? <StrictMode><PasswordResetConfirm /></StrictMode> : <PasswordResetConfirm />);
}
function fill(newPassword = password, confirmation = newPassword) {
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: newPassword } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirmation } });
}
async function submit() { fireEvent.click(screen.getByRole("button", { name: "Update password" })); }

describe("human email-link password reset", () => {
  it("keeps an unsubmitted link across the actual session-owner keyed remount", () => {
    history.replaceState(null, "", `/reset-password#token=${token}`);
    capturePasswordResetLink(); auth.pathname = "/reset-password";
    const view = render(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    const firstField = screen.getByLabelText("New password");
    expect(location.hash).toBe("");
    auth.status = "unauthenticated";
    view.rerender(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    expect(screen.getByLabelText("New password")).not.toBe(firstField);
    expect(screen.getByLabelText("Confirm new password")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body.innerHTML).not.toContain(token);
    expect(fetch).not.toHaveBeenCalled(); expect(auth.csrf).not.toHaveBeenCalled();
  });
  it("clears a posted link before an owner remount and cannot send an uncertain reset again", async () => {
    history.replaceState(null, "", `/reset-password#token=${token}`);
    capturePasswordResetLink(); auth.pathname = "/reset-password";
    const view = render(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    fill();
    let resolve: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(() => new Promise((done) => { resolve = done; }));
    await submit(); await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(readPasswordResetToken()).toBeNull();
    expect(readPasswordResetLink().outcome).toBe("submitted");
    auth.status = "unauthenticated";
    view.rerender(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    expect(screen.queryByLabelText("New password")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("could not confirm the reset outcome");
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/logout");
    expect(screen.getByRole("link", { name: "Contact support" })).toHaveAttribute("href", "/contact");
    expect(screen.queryByRole("link", { name: "Request a new link" })).toBeNull();
    await act(async () => resolve?.(Response.json({ status: "PASSWORD_RESET" })));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(document.body.innerHTML).not.toContain(token);
  });
  it.each([
    { status: "PASSWORD_RESET", code: 200, outcome: "complete", action: "Continue to sign in" },
    { status: "RESET_LINK_INVALID", code: 400, outcome: "invalid", action: "Request a new link" },
    { status: "RESET_UNAVAILABLE", code: 503, outcome: "uncertain", action: "Contact support" },
  ])("retains the finite $outcome outcome across an owner remount without retaining proof", async ({ status, code, outcome, action }) => {
    history.replaceState(null, "", `/reset-password#token=${token}`);
    capturePasswordResetLink(); auth.pathname = "/reset-password";
    const view = render(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    fill(); vi.mocked(fetch).mockResolvedValue(Response.json({ status }, { status: code })); await submit();
    await screen.findByRole("link", { name: action });
    auth.status = "unauthenticated";
    view.rerender(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    expect(screen.getByRole("link", { name: action })).toBeInTheDocument();
    expect(screen.queryByLabelText("New password")).toBeNull();
    expect(readPasswordResetLink()).toEqual(expect.objectContaining({ token: null, outcome }));
    expect(document.body.innerHTML).not.toContain(token); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("clears unsubmitted proof on an observed route exit before returning without a new link", () => {
    history.replaceState(null, "", `/reset-password#token=${token}`);
    capturePasswordResetLink(); auth.pathname = "/reset-password";
    const view = render(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    history.replaceState(null, "", "/forgot-password"); auth.pathname = "/forgot-password";
    view.rerender(<SessionProviderWrapper><p>Request another reset link</p></SessionProviderWrapper>);
    history.replaceState(null, "", "/reset-password"); auth.pathname = "/reset-password";
    view.rerender(<SessionProviderWrapper><PasswordResetConfirm /></SessionProviderWrapper>);
    expect(screen.queryByLabelText("New password")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("invalid, has expired, or has already been used");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not clear a newer captured link when an older request completes", async () => {
    confirm(); fill();
    let resolve: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(() => new Promise((done) => { resolve = done; }));
    await submit(); await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const next = "N".repeat(43);
    history.replaceState(null, "", `/reset-password#token=${next}`); capturePasswordResetLink();
    await act(async () => resolve?.(Response.json({ status: "PASSWORD_RESET" })));
    await screen.findByRole("link", { name: "Continue to sign in" });
    expect(readPasswordResetToken()).toBe(next);
    expect(readPasswordResetLink().outcome).toBeNull();
    expect(location.hash).toBe(""); expect(document.body.innerHTML).not.toContain(next);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("refuses an old capture before POST when a newer link arrives during CSRF preparation", async () => {
    confirm(); fill();
    let resolve: ((csrf: string) => void) | undefined;
    auth.csrf.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    await submit(); await waitFor(() => expect(auth.csrf).toHaveBeenCalledTimes(1));
    const next = "N".repeat(43);
    history.replaceState(null, "", `/reset-password#token=${next}`); capturePasswordResetLink();
    await act(async () => resolve?.("c".repeat(64)));
    expect(await screen.findByRole("alert")).toHaveTextContent("reset was not sent");
    expect(fetch).not.toHaveBeenCalled();
    expect(readPasswordResetLink()).toEqual(expect.objectContaining({ token: next, outcome: null }));
  });
  it("starts no email request automatically and only explicitly resends a generic request", async () => {
    render(<PasswordResetRequest />);
    expect(fetch).not.toHaveBeenCalled(); expect(auth.csrf).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@example.invalid" } });
    vi.mocked(fetch).mockResolvedValue(Response.json({ status: "RESET_LINK_REQUESTED" }, { status: 202 }));
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    await screen.findByText("Check your email");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("If an eligible account uses this address");
    expect(screen.getByText(/expires in 15 minutes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Request another link" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(vi.mocked(fetch).mock.calls.every(([url]) => url === "/api/password-reset/request")).toBe(true);
  });
  it("does not consume a link on GET/mount, retains it through StrictMode, and submits once only after human input", async () => {
    confirm(true);
    expect(location.hash).toBe("");
    expect(document.documentElement.innerHTML).not.toContain(token);
    expect(fetch).not.toHaveBeenCalled(); expect(auth.csrf).not.toHaveBeenCalled();
    fill();
    let resolve: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const button = screen.getByRole("button", { name: "Update password" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch).toHaveBeenCalledWith("/api/password-reset/confirm", expect.objectContaining({ method: "POST", credentials: "same-origin", redirect: "error", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "c".repeat(64) },
      body: JSON.stringify({ token, new_password: password }) }));
    await act(async () => resolve?.(Response.json({ status: "PASSWORD_RESET" })));
    const link = await screen.findByRole("link", { name: "Continue to sign in" });
    expect(link).toHaveAttribute("href", "/logout");
    expect(screen.queryByDisplayValue(password)).toBeNull();
    expect(screen.queryByRole("button", { name: "Update password" })).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("never presents a form or calls the API for missing/malformed link proof", () => {
    history.replaceState(null, "", "/reset-password#token=bad");
    render(<PasswordResetConfirm />);
    expect(screen.getByRole("alert")).toHaveTextContent("invalid, has expired, or has already been used");
    expect(screen.getByRole("link", { name: "Request a new link" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.queryByLabelText("New password")).toBeNull(); expect(fetch).not.toHaveBeenCalled();
    expect(location.hash).toBe("");
  });
  it("checks matching passwords and length locally before obtaining CSRF or posting", async () => {
    confirm(); fill(password, "different-password"); await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
    fill("short"); await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("10 and 128");
    expect(auth.csrf).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });
  it("replaces a used/expired result with a new-link action and cannot repeat confirmation", async () => {
    confirm(); fill();
    vi.mocked(fetch).mockResolvedValue(Response.json({ status: "RESET_LINK_INVALID" }, { status: 400 }));
    await submit();
    await screen.findByRole("link", { name: "Request a new link" });
    expect(screen.queryByRole("button", { name: "Update password" })).toBeNull();
    expect(screen.queryByDisplayValue(password)).toBeNull(); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it.each(["network", "unavailable", "malformed"])("treats %s after final send as unknown without replay or auto-login", async (failure) => {
    confirm(); fill();
    if (failure === "network") vi.mocked(fetch).mockRejectedValue(new Error(`private ${token}`));
    else vi.mocked(fetch).mockResolvedValue(Response.json(failure === "malformed" ? { status: "PASSWORD_RESET", token } : { status: "RESET_UNAVAILABLE" }, { status: failure === "malformed" ? 200 : 503 }));
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("This page will not send the reset again");
    expect(screen.queryByRole("button", { name: "Update password" })).toBeNull();
    expect(screen.getByRole("link", { name: "Contact support" })).toHaveAttribute("href", "/contact");
    expect(screen.queryByRole("link", { name: "Request a new link" })).toBeNull();
    expect(screen.queryByDisplayValue(password)).toBeNull(); expect(document.body.innerHTML).not.toContain(token);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("does not send with a missing CSRF and permits a later explicit safe attempt", async () => {
    confirm(); fill(); auth.csrf.mockResolvedValueOnce(null); await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("reset was not sent");
    expect(fetch).not.toHaveBeenCalled();
    fill(); vi.mocked(fetch).mockResolvedValue(Response.json({ status: "PASSWORD_RESET" })); await submit();
    await screen.findByRole("link", { name: "Continue to sign in" }); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("serializes request double clicks and exposes rate limits without account enumeration", async () => {
    render(<PasswordResetRequest />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@example.invalid" } });
    vi.mocked(fetch).mockResolvedValue(Response.json({ status: "RATE_LIMITED" }, { status: 429 }));
    const button = screen.getByRole("button", { name: "Send reset link" }); fireEvent.click(button); fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("Please wait"); expect(fetch).toHaveBeenCalledTimes(1);
  });
});
