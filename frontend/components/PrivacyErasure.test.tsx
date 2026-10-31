import React, { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PrivacyErasureConfirm, PrivacyErasureRequest } from "./PrivacyErasure";
import SessionProviderWrapper from "./SessionProviderWrapper";
import { capturePrivacyErasureLink, clearPrivacyErasureLink, readPrivacyErasureLink } from "../lib/privacyErasureLink";

const auth = vi.hoisted(() => ({ csrf: vi.fn(), status: "loading" as "loading" | "unauthenticated", pathname: "/" }));
vi.mock("next-auth/react", () => ({ getCsrfToken: auth.csrf, useSession: () => ({ data: null, status: auth.status }),
  SessionProvider: ({ children }: { children: React.ReactNode }) => children }));
vi.mock("next/navigation", () => ({ usePathname: () => auth.pathname }));
const token = "E".repeat(43);
beforeEach(() => {
  clearPrivacyErasureLink(); history.replaceState(null, "", "/");
  auth.status = "loading"; auth.pathname = "/";
  auth.csrf.mockReset().mockResolvedValue("c".repeat(64));
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => { cleanup(); clearPrivacyErasureLink(); vi.unstubAllGlobals(); vi.restoreAllMocks(); history.replaceState(null, "", "/"); });
function confirm() {
  history.replaceState(null, "", `/privacy-erasure/confirm#token=${token}`);
  capturePrivacyErasureLink();
  return render(<StrictMode><PrivacyErasureConfirm /></StrictMode>);
}
function choose() { fireEvent.click(screen.getByRole("checkbox", { name: "I want to permanently delete this account and its saved data." })); }

describe("explicit owner erasure recovery", () => {
  it("makes no automatic request and sends only a generic email-link request on a human action", async () => {
    render(<PrivacyErasureRequest />);
    expect(fetch).not.toHaveBeenCalled(); expect(auth.csrf).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "synthetic@example.invalid" } });
    vi.mocked(fetch).mockResolvedValue(Response.json({ status: "ERASURE_LINK_REQUESTED" }, { status: 202 }));
    fireEvent.click(screen.getByRole("button", { name: "Send confirmation link" }));
    await screen.findByText("Check your email");
    expect(screen.getByRole("status")).toHaveTextContent("If this address is eligible");
    expect(screen.getByRole("status")).toHaveTextContent("10 minutes");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith("/api/privacy-erasure/request", expect.objectContaining({ body: JSON.stringify({ email: "synthetic@example.invalid" }) }));
  });
  it("mounts without consuming proof and requires unchecked explicit consent, serializing double clicks", async () => {
    confirm();
    expect(location.hash).toBe(""); expect(document.documentElement.innerHTML).not.toContain(token);
    expect(fetch).not.toHaveBeenCalled(); expect(auth.csrf).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    const button = screen.getByRole("button", { name: "Permanently delete account" });
    expect(button).toBeDisabled(); choose();
    let resolve: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(() => new Promise((done) => { resolve = done; }));
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch).toHaveBeenCalledWith("/api/privacy-erasure/confirm", expect.objectContaining({ method: "POST", credentials: "same-origin", redirect: "error", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "c".repeat(64) }, body: JSON.stringify({ token, confirm_erasure: true }) }));
    await act(async () => resolve?.(Response.json({ status: "ACCOUNT_ERASED" })));
    expect(await screen.findByRole("heading", { name: "Account data deleted" })).toBeVisible();
    expect(readPrivacyErasureLink().token).toBeNull(); expect(screen.queryByRole("checkbox")).toBeNull();
  });
  it("retains unknown same-token outcome without replay and requires a new explicit choice for a manual attempt", async () => {
    const view = confirm(); choose();
    vi.mocked(fetch).mockRejectedValueOnce(new Error(`private ${token}`));
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing will be sent automatically");
    expect(readPrivacyErasureLink()).toEqual(expect.objectContaining({ token, outcome: "uncertain" }));
    expect(fetch).toHaveBeenCalledTimes(1); expect(document.body.innerHTML).not.toContain(token);
    view.unmount(); render(<PrivacyErasureConfirm />);
    const retry = screen.getByRole("button", { name: "Check or finish this deletion" });
    expect(retry).toBeDisabled(); expect(fetch).toHaveBeenCalledTimes(1);
    choose(); vi.mocked(fetch).mockResolvedValueOnce(Response.json({ status: "ACCOUNT_ERASED" }));
    fireEvent.click(retry);
    await screen.findByRole("heading", { name: "Account data deleted" });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.body === JSON.stringify({ token, confirm_erasure: true }))).toBe(true);
  });
  it("preserves proof through the real auth-owner remount and clears it on route exit", async () => {
    history.replaceState(null, "", `/privacy-erasure/confirm#token=${token}`);
    capturePrivacyErasureLink(); auth.pathname = "/privacy-erasure/confirm";
    const view = render(<SessionProviderWrapper><PrivacyErasureConfirm /></SessionProviderWrapper>);
    const first = screen.getByRole("checkbox");
    auth.status = "unauthenticated";
    view.rerender(<SessionProviderWrapper><PrivacyErasureConfirm /></SessionProviderWrapper>);
    expect(screen.getByRole("checkbox")).not.toBe(first);
    expect(readPrivacyErasureLink().token).toBe(token);
    expect(fetch).not.toHaveBeenCalled();
    auth.pathname = "/privacy-erasure"; history.replaceState(null, "", auth.pathname);
    view.rerender(<SessionProviderWrapper><PrivacyErasureRequest /></SessionProviderWrapper>);
    expect(readPrivacyErasureLink().token).toBeNull();
  });
  it.each(["missing", "malformed", "query"])("does not offer deletion for a %s link", (kind) => {
    history.replaceState(null, "", kind === "missing" ? "/privacy-erasure/confirm" : kind === "query"
      ? `/privacy-erasure/confirm?token=${token}` : "/privacy-erasure/confirm#token=bad");
    render(<PrivacyErasureConfirm />);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("link", { name: "Request a new link" })).toHaveAttribute("href", "/privacy-erasure");
    expect(fetch).not.toHaveBeenCalled(); expect(auth.csrf).not.toHaveBeenCalled();
  });
  it("requires a current CSRF and refuses stale link capture before sending", async () => {
    confirm(); choose();
    let resolve: ((value: string) => void) | undefined;
    auth.csrf.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete account" }));
    await waitFor(() => expect(auth.csrf).toHaveBeenCalledOnce());
    const next = "N".repeat(43);
    history.replaceState(null, "", `/privacy-erasure/confirm#token=${next}`); capturePrivacyErasureLink();
    await act(async () => resolve?.("c".repeat(64)));
    expect(await screen.findByRole("alert")).toHaveTextContent("confirmation was not sent");
    expect(fetch).not.toHaveBeenCalled(); expect(readPrivacyErasureLink().token).toBe(next);
  });
  it.each([[400, "ERASURE_LINK_INVALID"], [429, "RATE_LIMITED"], [422, "ERASURE_REQUEST_REJECTED"]] as const)("handles finite %s result without inventing completion", async (status, result) => {
    confirm(); choose(); vi.mocked(fetch).mockResolvedValue(Response.json({ status: result }, { status }));
    fireEvent.click(screen.getByRole("button", { name: "Permanently delete account" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("heading", { name: "Account data deleted" })).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
    if (status === 400) expect(screen.queryByRole("checkbox")).toBeNull();
    else expect(screen.getByRole("checkbox")).not.toBeChecked();
  });
});
