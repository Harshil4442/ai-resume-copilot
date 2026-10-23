import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ status: "authenticated", email: "owned@example.invalid", csrf: vi.fn() }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: auth.status, data: { user: { email: auth.email } } }), getCsrfToken: auth.csrf }));
vi.mock("next/script", () => ({ default: ({ src, onReady, onError }: { src: string; onReady: () => void; onError: () => void }) =>
  <div data-testid="google-script" data-src={src}><button type="button" onClick={onReady}>Load Google verification</button><button type="button" onClick={onError}>Fail Google verification</button></div> }));

import CandidateAccountEnrollment from "./CandidateAccountEnrollment";

const challenge = { challenge_id: "a6d9dc99-84fa-4a52-af5a-0ed921e7b048", nonce: "n".repeat(43),
  client_id: "123456-owned_client.apps.googleusercontent.com", expires_at_ms: 0 };
const idToken = "owned-google-token-".repeat(10);
const password = "owned-password-123";
const consent = /I want to enroll my existing account/;
let googleCallback: ((reply: { credential?: string }) => void) | null;
let initialize: ReturnType<typeof vi.fn>;
let renderButton: ReturnType<typeof vi.fn>;

beforeEach(() => {
  auth.status = "authenticated"; auth.email = "owned@example.invalid"; auth.csrf.mockReset().mockResolvedValue("c".repeat(64));
  challenge.expires_at_ms = Date.now() + 300000;
  googleCallback = null;
  initialize = vi.fn((value) => { googleCallback = value.callback; });
  renderButton = vi.fn();
  vi.stubGlobal("google", { accounts: { id: { initialize, renderButton, cancel: vi.fn() } } });
  vi.stubGlobal("fetch", vi.fn(async (url) => Response.json(String(url).endsWith("/availability")
    ? { fresh_registration: true, legacy_enrollment: true } : { status: "GOOGLE_ENROLLMENT_AVAILABLE" })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function ready() {
  render(<CandidateAccountEnrollment />);
  await screen.findByRole("radio", { name: "Google" });
}

async function verifiedGoogle(request: ReturnType<typeof vi.fn>) {
  request.mockResolvedValueOnce(Response.json(challenge));
  fireEvent.change(screen.getByLabelText("Choose a HireWiz password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("checkbox", { name: consent }));
  fireEvent.click(screen.getByRole("button", { name: "Verify my Google account" }));
  await screen.findByTestId("google-script");
  fireEvent.click(screen.getByRole("button", { name: "Load Google verification" }));
  act(() => googleCallback?.({ credential: idToken }));
  expect(screen.getByText(/Google verification received/)).toBeInTheDocument();
}

describe("explicit enrollment of the same existing account", () => {
  it("waits for owner resolution and never loads Google or starts a challenge automatically", async () => {
    auth.status = "loading";
    const request = vi.mocked(fetch);
    const view = render(<CandidateAccountEnrollment />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking your account");
    expect(request).not.toHaveBeenCalled();
    expect(screen.queryByTestId("google-script")).toBeNull();
    auth.status = "unauthenticated"; view.rerender(<CandidateAccountEnrollment />);
    expect(screen.getByRole("link", { name: "Sign in to continue" })).toHaveAttribute("href", "/login");
    expect(request).not.toHaveBeenCalled();
  });

  it.each(["ELIGIBLE", "UNAVAILABLE", "PASSWORD_SIGN_IN_REQUIRED"])("does not offer another enrollment from %s", async (status) => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ status }));
    render(<CandidateAccountEnrollment />);
    await waitFor(() => expect(screen.queryByText("Checking your account…")).toBeNull());
    expect(screen.queryByRole("button", { name: "Enroll this account" })).toBeNull();
    expect(screen.queryByTestId("google-script")).toBeNull();
  });

  it("uses the exact Google nonce and requires a separate consented enrollment click", async () => {
    const request = vi.mocked(fetch);
    await ready();
    expect(screen.getByRole("link", { name: "Forgot your HireWiz password?" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.getByRole("button", { name: "Verify my Google account" })).toBeDisabled();
    expect(screen.queryByTestId("google-script")).toBeNull();
    await verifiedGoogle(request);
    expect(screen.getByTestId("google-script")).toHaveAttribute("data-src", "https://accounts.google.com/gsi/client");
    expect(initialize).toHaveBeenCalledWith(expect.objectContaining({ client_id: challenge.client_id, nonce: challenge.nonce, auto_select: false, ux_mode: "popup" }));
    expect(renderButton).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledTimes(3);
    expect(JSON.parse(request.mock.calls[2][1]!.body as string)).toEqual({});
    expect(request.mock.calls[2][1]!.headers).toEqual({ "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "c".repeat(64) });
    request.mockResolvedValueOnce(Response.json({ status: "ENROLLED" }));
    fireEvent.click(screen.getByRole("button", { name: "Enroll this account" }));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(request.mock.calls[3][0]).toBe("/api/candidate-account/enroll-google");
    expect(JSON.parse(request.mock.calls[3][1]!.body as string)).toEqual({ challenge_id: challenge.challenge_id,
      nonce: challenge.nonce, id_token: idToken, password, accepted_enrollment: true });
    expect(request).toHaveBeenCalledTimes(4);
    expect(screen.queryByDisplayValue(password)).toBeNull();
    expect(screen.queryByText(idToken)).toBeNull();
  });

  it("includes the current password only when the Google account owner explicitly supplies it", async () => {
    const request = vi.mocked(fetch);
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: "I already have a HireWiz password" }));
    await verifiedGoogle(request);
    expect(screen.getByRole("button", { name: "Enroll this account" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Existing HireWiz password"), { target: { value: "existing-owned-password" } });
    request.mockResolvedValueOnce(Response.json({ status: "ENROLLED" }));
    fireEvent.click(screen.getByRole("button", { name: "Enroll this account" }));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(JSON.parse(request.mock.calls[3][1]!.body as string).current_password).toBe("existing-owned-password");
    expect(screen.queryByDisplayValue("existing-owned-password")).toBeNull();
  });

  it("enrolls a legacy password account only after current password and explicit consent", async () => {
    const request = vi.mocked(fetch);
    await ready();
    fireEvent.click(screen.getByRole("radio", { name: "HireWiz password" }));
    fireEvent.change(screen.getByLabelText("Current HireWiz password"), { target: { value: password } });
    expect(screen.getByRole("button", { name: "Enroll this account" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: consent }));
    request.mockResolvedValueOnce(Response.json({ status: "ENROLLED" }));
    fireEvent.click(screen.getByRole("button", { name: "Enroll this account" }));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(request.mock.calls[2][0]).toBe("/api/candidate-account/enroll-password");
    expect(JSON.parse(request.mock.calls[2][1]!.body as string)).toEqual({ password, accepted_enrollment: true });
    expect(screen.getByText(/Enrollment is complete for your existing account/)).not.toHaveTextContent("Google");
    expect(initialize).not.toHaveBeenCalled();
  });

  it("retains an uncertain send as status-only recovery and never repeats enrollment", async () => {
    const request = vi.mocked(fetch);
    await ready();
    await verifiedGoogle(request);
    request.mockRejectedValueOnce(new Error("Owned lost response"));
    fireEvent.click(screen.getByRole("button", { name: "Enroll this account" }));
    await screen.findByRole("button", { name: "Check enrollment status" });
    expect(screen.getByRole("alert")).toHaveTextContent("Do not send enrollment again");
    expect(screen.queryByRole("button", { name: "Enroll this account" })).toBeNull();
    expect(screen.getByLabelText("Account email")).toHaveValue(auth.email);
    expect(screen.getByLabelText("HireWiz password for status")).toHaveValue("");
    act(() => googleCallback?.({ credential: idToken }));
    expect(request).toHaveBeenCalledTimes(4);
    fireEvent.change(screen.getByLabelText("HireWiz password for status"), { target: { value: password } });
    request.mockResolvedValueOnce(Response.json({ status: "PENDING_REVIEW_REQUIRED" }));
    fireEvent.click(screen.getByRole("button", { name: "Check enrollment status" }));
    await screen.findByText(/Enrollment needs review/);
    expect(request.mock.calls[4][0]).toBe("/api/candidate-account/registration-status");
    expect(screen.getByLabelText("HireWiz password for status")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Enroll this account" })).toBeNull();
  });

  it("recovers a confirmed enrollment only through an explicit read-only status request", async () => {
    const request = vi.mocked(fetch);
    await ready();
    fireEvent.click(screen.getByRole("radio", { name: "HireWiz password" }));
    fireEvent.change(screen.getByLabelText("Current HireWiz password"), { target: { value: password } });
    fireEvent.click(screen.getByRole("checkbox", { name: consent }));
    request.mockResolvedValueOnce(Response.json({ status: "UNAVAILABLE" }, { status: 503 }));
    fireEvent.click(screen.getByRole("button", { name: "Enroll this account" }));
    await screen.findByRole("button", { name: "Check enrollment status" });
    fireEvent.change(screen.getByLabelText("HireWiz password for status"), { target: { value: password } });
    request.mockResolvedValueOnce(Response.json({ status: "ENROLLED" }));
    fireEvent.click(screen.getByRole("button", { name: "Check enrollment status" }));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(request.mock.calls[3][0]).toBe("/api/candidate-account/registration-status");
    expect(request).toHaveBeenCalledTimes(4);
  });

  it.each(["expired", "wrong_client", "extra"])("refuses a malformed Google challenge: %s", async (fault) => {
    const request = vi.mocked(fetch);
    await ready();
    const invalid = { ...challenge, ...(fault === "expired" ? { expires_at_ms: Date.now() - 1 }
      : fault === "wrong_client" ? { client_id: "https://foreign.invalid/identity" } : { id_token: "private" }) };
    request.mockResolvedValueOnce(Response.json(invalid));
    fireEvent.click(screen.getByRole("checkbox", { name: consent }));
    fireEvent.click(screen.getByRole("button", { name: "Verify my Google account" }));
    await screen.findByRole("alert");
    expect(screen.queryByTestId("google-script")).toBeNull();
    expect(initialize).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("rejects expiry before enrollment and ignores a callback after changing methods", async () => {
    const request = vi.mocked(fetch);
    await ready(); await verifiedGoogle(request);
    vi.spyOn(Date, "now").mockReturnValue(challenge.expires_at_ms);
    fireEvent.click(screen.getByRole("button", { name: "Enroll this account" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Complete fresh Google verification");
    expect(request).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("radio", { name: "HireWiz password" }));
    act(() => googleCallback?.({ credential: idToken }));
    expect(screen.getByRole("button", { name: "Enroll this account" })).toBeDisabled();
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("serializes double clicks while a single enrollment request is in flight", async () => {
    const request = vi.mocked(fetch);
    await ready(); await verifiedGoogle(request);
    let finish: ((value: Response) => void) | undefined;
    request.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const button = screen.getByRole("button", { name: "Enroll this account" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    await act(async () => finish?.(Response.json({ status: "ENROLLED" })));
    await screen.findByRole("link", { name: "Sign out before signing in again" });
    expect(request).toHaveBeenCalledTimes(4);
  });

  it.each([
    { fresh_registration: false, legacy_enrollment: false },
    { fresh_registration: true, legacy_enrollment: "true" },
    { fresh_registration: true, legacy_enrollment: true, extra: true },
  ])("requires a strict fresh availability response before offering enrollment: %j", async (availability) => {
    vi.mocked(fetch).mockImplementation(async (url) => Response.json(String(url).endsWith("/availability")
      ? availability : { status: "GOOGLE_ENROLLMENT_AVAILABLE" }));
    render(<CandidateAccountEnrollment />);
    await screen.findByText(/Account enrollment is currently unavailable/);
    expect(screen.queryByRole("button", { name: "Enroll this account" })).toBeNull();
    expect(screen.queryByTestId("google-script")).toBeNull();
    expect(auth.csrf).not.toHaveBeenCalled();
  });
});
