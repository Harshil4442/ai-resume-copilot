import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ status: "authenticated", id: "12", email: "owned@example.invalid", csrf: vi.fn() }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: auth.status, data: { user: { id: auth.id, email: auth.email } } }), getCsrfToken: auth.csrf }));
vi.mock("next/script", () => ({ default: ({ src, onReady, onError }: { src: string; onReady: () => void; onError: () => void }) =>
  <div data-testid="google-script" data-src={src}><button type="button" onClick={onReady}>Load Google verification</button><button type="button" onClick={onError}>Fail Google verification</button></div> }));

import CandidateAccountRecovery from "./CandidateAccountRecovery";

const challenge = { challenge_id: "a6d9dc99-84fa-4a52-af5a-0ed921e7b048", nonce: "n".repeat(43),
  client_id: "123456-owned_client.apps.googleusercontent.com", expires_at_ms: 0 };
const idToken = "owned-google-recovery-token-".repeat(10);
const password = "new-owned-password-123";
const consent = /I want to replace my forgotten HireWiz password/;
let callback: ((reply: { credential?: string }) => void) | null;
let initialize: ReturnType<typeof vi.fn>;

beforeEach(() => {
  auth.status = "authenticated"; auth.id = "12"; auth.email = "owned@example.invalid";
  auth.csrf.mockReset().mockResolvedValue("c".repeat(64));
  challenge.expires_at_ms = Date.now() + 300000; callback = null;
  initialize = vi.fn((value) => { callback = value.callback; });
  vi.stubGlobal("google", { accounts: { id: { initialize, renderButton: vi.fn(), cancel: vi.fn() } } });
  vi.stubGlobal("fetch", vi.fn(async (url) => Response.json(String(url).endsWith("/availability")
    ? { fresh_registration: true, legacy_enrollment: true } : { status: "GOOGLE_ENROLLMENT_AVAILABLE" })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function ready() {
  const view = render(<CandidateAccountRecovery />);
  await screen.findByLabelText("New HireWiz password");
  return view;
}

async function verify(request: ReturnType<typeof vi.fn>) {
  fireEvent.change(screen.getByLabelText("New HireWiz password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm new HireWiz password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("checkbox", { name: consent }));
  request.mockResolvedValueOnce(Response.json(challenge));
  fireEvent.click(screen.getByRole("button", { name: "Verify Google for recovery" }));
  await screen.findByTestId("google-script");
  fireEvent.click(screen.getByRole("button", { name: "Load Google verification" }));
  act(() => callback?.({ credential: idToken }));
  expect(screen.getByText(/Google verification received/)).toBeInTheDocument();
}

describe("explicit authoritative-Google recovery of an unenrolled account", () => {
  it("offers ordinary Google sign-in first and starts no recovery automatically", () => {
    auth.status = "unauthenticated";
    render(<CandidateAccountRecovery />);
    expect(screen.getByRole("link", { name: "Sign in with Google to continue recovery" })).toHaveAttribute("href", "/login?returnTo=%2Faccount-recovery");
    expect(fetch).not.toHaveBeenCalled();
    expect(auth.csrf).not.toHaveBeenCalled();
    expect(screen.queryByTestId("google-script")).toBeNull();
  });

  it.each(["ELIGIBLE", "LEGACY_ENROLLMENT_UNAVAILABLE", "UNAVAILABLE"])("does not offer recovery from %s", async (status) => {
    vi.mocked(fetch).mockImplementation(async (url) => Response.json(String(url).endsWith("/availability")
      ? { fresh_registration: true, legacy_enrollment: true } : { status }));
    render(<CandidateAccountRecovery />);
    await waitFor(() => expect(screen.queryByText("Checking your account…")).toBeNull());
    expect(screen.queryByRole("button", { name: "Replace password and enroll this account" })).toBeNull();
    expect(screen.queryByTestId("google-script")).toBeNull();
    expect(auth.csrf).not.toHaveBeenCalled();
    if (status === "ELIGIBLE") expect(screen.getByRole("status")).toHaveTextContent("already enrolled");
    if (status === "LEGACY_ENROLLMENT_UNAVAILABLE") expect(screen.getByRole("link", { name: "Sign in with Google to continue recovery" })).toBeInTheDocument();
  });

  it("requires consent, same challenge nonce, matching human-entered passwords and a separate final click", async () => {
    const request = vi.mocked(fetch);
    await ready();
    expect(screen.getByRole("button", { name: "Verify Google for recovery" })).toBeDisabled();
    expect(screen.queryByTestId("google-script")).toBeNull();
    expect(request).toHaveBeenCalledTimes(2);
    await verify(request);
    expect(initialize).toHaveBeenCalledWith(expect.objectContaining({ client_id: challenge.client_id, nonce: challenge.nonce, auto_select: false, ux_mode: "popup" }));
    expect(request.mock.calls[2][0]).toBe("/api/candidate-account/recovery-google-challenge");
    expect(JSON.parse(request.mock.calls[2][1]!.body as string)).toEqual({});
    expect(request).toHaveBeenCalledTimes(3);
    fireEvent.change(screen.getByLabelText("Confirm new HireWiz password"), { target: { value: "different-password" } });
    expect(screen.getByRole("button", { name: "Replace password and enroll this account" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Confirm new HireWiz password"), { target: { value: password } });
    request.mockResolvedValueOnce(Response.json({ status: "ENROLLED" }));
    fireEvent.click(screen.getByRole("button", { name: "Replace password and enroll this account" }));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(request.mock.calls[3][0]).toBe("/api/candidate-account/recover-google");
    expect(JSON.parse(request.mock.calls[3][1]!.body as string)).toEqual({ challenge_id: challenge.challenge_id, nonce: challenge.nonce,
      id_token: idToken, password, accepted_enrollment: true, accepted_recovery: true });
    expect(request.mock.calls[3][1]!.headers).toEqual({ "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "c".repeat(64) });
    expect(request).toHaveBeenCalledTimes(4);
    expect(screen.queryByDisplayValue(password)).toBeNull();
    expect(screen.queryByText(idToken)).toBeNull();
  });

  it.each([
    [401, "RECOVERY_AUTHENTICATION_FAILED", "proof expired"],
    [403, "RECOVERY_EMAIL_PROOF_REQUIRED", "cannot verify current ownership"],
    [409, "RECOVERY_UNAVAILABLE", "older accounts"],
    [422, "RECOVERY_REQUEST_REJECTED", "rejected before enrollment"],
    [429, "RATE_LIMITED", "Too many account requests"],
  ])("explains the definite %s refusal without treating it as a lost enrollment", async (status, code, message) => {
    const request = vi.mocked(fetch);
    await ready(); await verify(request);
    request.mockResolvedValueOnce(Response.json({ status: code }, { status: Number(status) }));
    fireEvent.click(screen.getByRole("button", { name: "Replace password and enroll this account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(String(message));
    expect(screen.queryByRole("button", { name: "Check enrollment status" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Replace password and enroll this account" })).toBeNull();
    expect(screen.getByRole("link", { name: "Use my existing HireWiz password" })).toHaveAttribute("href", "/account-enrollment");
    expect(screen.getByRole("link", { name: "Contact support" })).toHaveAttribute("href", "/contact");
    expect(screen.queryByDisplayValue(password)).toBeNull();
    expect(request).toHaveBeenCalledTimes(4);
  });

  it.each(["network", "503", "malformed"])("keeps %s after final send status-only and never repeats recovery", async (fault) => {
    const request = vi.mocked(fetch);
    await ready(); await verify(request);
    if (fault === "network") request.mockRejectedValueOnce(new Error("synthetic lost response"));
    else request.mockResolvedValueOnce(Response.json(fault === "503" ? { status: "UNAVAILABLE" } : { status: "ENROLLED", private: "not public" }, { status: fault === "503" ? 503 : 200 }));
    fireEvent.click(screen.getByRole("button", { name: "Replace password and enroll this account" }));
    await screen.findByRole("button", { name: "Check enrollment status" });
    expect(screen.getByRole("alert")).toHaveTextContent("Do not submit recovery again");
    expect(screen.queryByRole("button", { name: "Replace password and enroll this account" })).toBeNull();
    expect(screen.getByLabelText("New HireWiz password for status")).toHaveValue("");
    act(() => callback?.({ credential: idToken }));
    expect(request).toHaveBeenCalledTimes(4);
    fireEvent.change(screen.getByLabelText("New HireWiz password for status"), { target: { value: password } });
    request.mockResolvedValueOnce(Response.json({ status: "PENDING_REVIEW_REQUIRED" }));
    fireEvent.click(screen.getByRole("button", { name: "Check enrollment status" }));
    await screen.findByText(/Recovery enrollment needs review/);
    expect(request.mock.calls[4][0]).toBe("/api/candidate-account/registration-status");
    expect(screen.getByLabelText("New HireWiz password for status")).toHaveValue("");
    expect(request).toHaveBeenCalledTimes(5);
  });

  it("refuses an expired proof before the final request", async () => {
    const request = vi.mocked(fetch);
    await ready(); await verify(request);
    vi.spyOn(Date, "now").mockReturnValue(challenge.expires_at_ms);
    fireEvent.click(screen.getByRole("button", { name: "Replace password and enroll this account" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Complete fresh Google verification");
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("explains an unsupported mailbox before loading any Google recovery script", async () => {
    const request = vi.mocked(fetch);
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: consent }));
    request.mockResolvedValueOnce(Response.json({ status: "RECOVERY_EMAIL_PROOF_REQUIRED" }, { status: 403 }));
    fireEvent.click(screen.getByRole("button", { name: "Verify Google for recovery" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use your existing HireWiz password or contact support");
    expect(screen.queryByTestId("google-script")).toBeNull();
    expect(initialize).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("serializes a double click into one recovery mutation", async () => {
    const request = vi.mocked(fetch);
    await ready(); await verify(request);
    let finish: ((value: Response) => void) | undefined;
    request.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const button = screen.getByRole("button", { name: "Replace password and enroll this account" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    await act(async () => finish?.(Response.json({ status: "ENROLLED" })));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(request).toHaveBeenCalledTimes(4);
  });

  it("drops all proof and password state when the signed-in owner changes", async () => {
    const request = vi.mocked(fetch);
    const view = await ready(); await verify(request);
    const oldCallback = callback;
    auth.id = "13"; auth.email = "different@example.invalid";
    view.rerender(<CandidateAccountRecovery />);
    await screen.findByLabelText("New HireWiz password");
    expect(screen.getByLabelText("New HireWiz password")).toHaveValue("");
    act(() => oldCallback?.({ credential: idToken }));
    expect(screen.getByRole("button", { name: "Replace password and enroll this account" })).toBeDisabled();
    expect(request.mock.calls.filter(([url]) => String(url).endsWith("/recover-google"))).toHaveLength(0);
  });
});
