import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import BrowserPairingReview from "./BrowserPairingReview";

const pairing = "00000000-0000-4000-8000-000000000001";
const challenge = { pairing_id: pairing, device_id: "00000000-0000-4000-8000-000000000002",
  challenge_id: "00000000-0000-4000-8000-000000000003", nonce: "00000000-0000-4000-8000-000000000004",
  operation: "confirm_pairing", key_sha256: "a".repeat(64), request_sha256: "b".repeat(64), expires_at_ms: Date.now() + 60000 };
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("candidate connection review", () => {
  it("does not request anything for an absent connection or before explicit review", async () => {
    const request = vi.fn(); vi.stubGlobal("fetch", request);
    const view = render(<BrowserPairingReview pairingId={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("fresh connection request");
    expect(request).not.toHaveBeenCalled();
    view.rerender(<BrowserPairingReview pairingId={pairing} />);
    expect(screen.getByRole("button", { name: "Review connection" })).toBeEnabled();
    expect(request).not.toHaveBeenCalled();
  });
  it("requires fingerprint confirmation and password before the single candidate-confirm POST", async () => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" })).mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json(challenge)).mockResolvedValueOnce(Response.json({ status: "CANDIDATE_CONFIRMED", pairing_id: pairing, device_id: challenge.device_id }));
    vi.stubGlobal("fetch", request);
    render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    const confirmation = await screen.findByRole("button", { name: "Confirm this device" });
    expect(confirmation).toBeDisabled();
    fireEvent.change(screen.getByLabelText("HireWiz password"), { target: { value: "synthetic-review-password" } });
    expect(confirmation).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(confirmation).toBeEnabled();
    await userEvent.click(confirmation);
    expect(await screen.findByRole("status")).toHaveTextContent("has not approved filling, uploading or submitting");
    expect(request).toHaveBeenCalledTimes(4);
    expect(request.mock.calls[3][0]).toBe("/api/browser-pairing/candidate/confirm");
    expect(JSON.parse(request.mock.calls[3][1].body)).toMatchObject({ protocol_version: 2, operation: "confirm_pairing", confirmed: true, challenge_id: challenge.challenge_id });
    expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });
  it("clears a possibly consumed review and never retries an unavailable confirmation automatically", async () => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" })).mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json(challenge)).mockResolvedValueOnce(Response.json({ detail: "untrusted-secret" }, { status: 503 }));
    vi.stubGlobal("fetch", request); render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    await screen.findByRole("button", { name: "Confirm this device" });
    fireEvent.change(screen.getByLabelText("HireWiz password"), { target: { value: "synthetic-review-password" } });
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: "Confirm this device" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("currently unavailable"));
    expect(screen.getByRole("alert")).not.toHaveTextContent("untrusted-secret");
    expect(request).toHaveBeenCalledTimes(4);
    expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });
  it("returns password sign-in to only this exact pairing without requesting a native challenge", async () => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "PASSWORD_SIGN_IN_REQUIRED" }));
    vi.stubGlobal("fetch", request); render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    expect(await screen.findByRole("link", { name: "Sign in and return to this connection" })).toHaveAttribute("href", `/login?returnTo=${encodeURIComponent(`/browser-companion?pairing=${pairing}`)}`);
    expect(request).toHaveBeenCalledTimes(1); expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });
  for (const status of ["LEGACY_ENROLLMENT_UNAVAILABLE", "GOOGLE_ENROLLMENT_AVAILABLE", "UNAVAILABLE"]) it(`does not bootstrap context from ${status}`, async () => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status }));
    vi.stubGlobal("fetch", request); render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    expect(await screen.findByRole("status")).toHaveTextContent(status === "UNAVAILABLE" ? "currently unavailable" : "Enroll your existing account");
    if (status !== "UNAVAILABLE") expect(screen.getByRole("link", { name: "Review account enrollment" })).toHaveAttribute("href", "/account-enrollment");
    expect(request).toHaveBeenCalledTimes(1); expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });
  it("rejects malformed eligibility instead of exposing backend fields or requesting a challenge", async () => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE", password: "untrusted-secret" }));
    vi.stubGlobal("fetch", request); render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("currently unavailable");
    expect(screen.getByRole("alert")).not.toHaveTextContent("untrusted-secret"); expect(request).toHaveBeenCalledTimes(1);
  });

  it("expires visibly, clears approval/password and makes no automatic network request", async () => {
    vi.useFakeTimers();
    const deadline = Date.now() + 60000;
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json({ ...challenge, expires_at_ms: deadline }));
    vi.stubGlobal("fetch", request);
    render(<BrowserPairingReview pairingId={pairing} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Review connection" })); });
    fireEvent.change(screen.getByLabelText("HireWiz password"), { target: { value: "synthetic-review-password" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Confirm this device" })).toBeEnabled();
    expect(screen.getByText(/This review expires at/)).toBeVisible();
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(screen.getByRole("status")).toHaveTextContent("This review expired");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByLabelText("HireWiz password")).toHaveValue("");
    expect(screen.getByLabelText("HireWiz password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Confirm this device" })).toBeDisabled();
    expect(screen.getByText(challenge.key_sha256)).toBeVisible();
    await act(async () => { vi.advanceTimersByTime(120000); });
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("checks expiry when a backgrounded review regains focus", async () => {
    vi.useFakeTimers();
    const deadline = Date.now() + 60000;
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json({ ...challenge, expires_at_ms: deadline }));
    vi.stubGlobal("fetch", request);
    render(<BrowserPairingReview pairingId={pairing} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Review connection" })); });
    vi.setSystemTime(deadline + 1);
    fireEvent.focus(window);
    expect(screen.getByRole("button", { name: "Confirm this device" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("This review expired");
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("renews only after an explicit click and requires fresh password and consent", async () => {
    vi.useFakeTimers();
    const now = Date.now();
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json({ ...challenge, expires_at_ms: now + 60000 }))
      .mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "d".repeat(64) }))
      .mockResolvedValueOnce(Response.json({ ...challenge, challenge_id: "00000000-0000-4000-8000-000000000005", expires_at_ms: now + 120000 }));
    vi.stubGlobal("fetch", request);
    render(<BrowserPairingReview pairingId={pairing} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Review connection" })); });
    fireEvent.change(screen.getByLabelText("HireWiz password"), { target: { value: "synthetic-review-password" } });
    fireEvent.click(screen.getByRole("checkbox"));
    await act(async () => { vi.advanceTimersByTime(60000); });
    expect(request).toHaveBeenCalledTimes(3);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Load a fresh review" })); });
    expect(request).toHaveBeenCalledTimes(6);
    expect(screen.queryByText(/This review expired/)).toBeNull();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByLabelText("HireWiz password")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Confirm this device" })).toBeDisabled();
    expect(request.mock.calls.every(([url]) => url !== "/api/browser-pairing/candidate/confirm")).toBe(true);
  });

  it.each([0, -1, 1.5, null, 8_640_000_000_000_001])("refuses an invalid expiry %s without offering confirmation", async (expires_at_ms) => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ status: "ELIGIBLE" }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json({ ...challenge, expires_at_ms }));
    vi.stubGlobal("fetch", request);
    render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check its current status in your companion");
    expect(screen.queryByLabelText("HireWiz password")).toBeNull();
    expect(request).toHaveBeenCalledTimes(3);
  });
});
