import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import BrowserPairingReview from "./BrowserPairingReview";

const pairing = "00000000-0000-4000-8000-000000000001";
const challenge = { pairing_id: pairing, device_id: "00000000-0000-4000-8000-000000000002",
  challenge_id: "00000000-0000-4000-8000-000000000003", nonce: "00000000-0000-4000-8000-000000000004",
  operation: "confirm_pairing", key_sha256: "a".repeat(64), request_sha256: "b".repeat(64), expires_at_ms: Date.now() + 60000 };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
    const request = vi.fn().mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
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
    expect(request).toHaveBeenCalledTimes(3);
    expect(request.mock.calls[2][0]).toBe("/api/browser-pairing/candidate/confirm");
    expect(JSON.parse(request.mock.calls[2][1].body)).toMatchObject({ protocol_version: 2, operation: "confirm_pairing", confirmed: true, challenge_id: challenge.challenge_id });
    expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });
  it("clears a possibly consumed review and never retries an unavailable confirmation automatically", async () => {
    const request = vi.fn().mockResolvedValueOnce(Response.json({ csrf_token: "c".repeat(64) }))
      .mockResolvedValueOnce(Response.json(challenge)).mockResolvedValueOnce(Response.json({ detail: "untrusted-secret" }, { status: 503 }));
    vi.stubGlobal("fetch", request); render(<BrowserPairingReview pairingId={pairing} />);
    await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
    await screen.findByRole("button", { name: "Confirm this device" });
    fireEvent.change(screen.getByLabelText("HireWiz password"), { target: { value: "synthetic-review-password" } });
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: "Confirm this device" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("currently unavailable"));
    expect(screen.getByRole("alert")).not.toHaveTextContent("untrusted-secret");
    expect(request).toHaveBeenCalledTimes(3);
    expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });
});
