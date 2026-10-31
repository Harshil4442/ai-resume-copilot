import { createHash, webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import vector from "../../backend/tests/fixtures/browser_submission_v5.json";
import { accountMutationHeaders } from "../lib/accountTransportClient";
import { SUBMISSION_BILLING_KEYS } from "../lib/browserSubmissionBilling";
import BrowserAssistanceReview from "./BrowserAssistanceReview";

vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));
vi.mock("../lib/accountTransportClient", () => ({ accountMutationHeaders: vi.fn() }));
vi.mock("../lib/api", () => ({ apiDownload: vi.fn() }));
const request = vi.fn();
let now = vector.unified5.lease_payload.issued_at_ms;
const review = () => structuredClone(vector.unified5.review_response);
function show() { return render(<BrowserAssistanceReview reviewId={vector.unified5.review_response.id} applicationId="apply_synthetic" deviceId={null} />); }
async function load() {
  fireEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  await screen.findByRole("heading", { name: "Final submission · 3 unified credits" });
}
const canonical = (value: unknown): string => JSON.stringify(value, (_key, entry) => entry && typeof entry === "object" && !Array.isArray(entry)
  ? Object.fromEntries(Object.entries(entry).sort(([a], [b]) => a.localeCompare(b))) : entry);
beforeEach(() => {
  vi.resetAllMocks(); now = vector.unified5.lease_payload.issued_at_ms;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  vi.stubGlobal("crypto", webcrypto); vi.stubGlobal("fetch", request);
  vi.mocked(accountMutationHeaders).mockResolvedValue({ "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "9".repeat(64) });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("displays the exact three-credit quote and sends only its explicit consent identifier", async () => {
  const original = review(); request.mockResolvedValueOnce(Response.json(original)).mockResolvedValueOnce(Response.json({ ...original, state: "approved" }));
  show(); await load();
  expect(screen.getByText(/Price version credit-services-2026-10-27-v1/)).toBeVisible();
  const approve = screen.getByRole("button", { name: "Approve final submission · reserve 3 unified credits" });
  expect(approve).toBeDisabled(); fireEvent.click(screen.getByRole("checkbox")); expect(approve).toBeEnabled();
  fireEvent.click(approve); await screen.findByText(/Final submission approved and 3 unified credits reserved/);
  expect(request).toHaveBeenCalledTimes(2);
  expect(JSON.parse(request.mock.calls[1][1].body)).toEqual(vector.unified5.approve_request);
  expect(request.mock.calls[1][1].redirect).toBe("error");
});
it.each(SUBMISSION_BILLING_KEYS)("does not display actionable consent when wire billing omits %s", async key => {
  const value: Record<string, unknown> = review(); const quote = value.billing as Record<string, unknown>; delete quote[key];
  request.mockResolvedValueOnce(Response.json(value)); show(); fireEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  await screen.findByRole("alert"); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument(); expect(request).toHaveBeenCalledTimes(1);
});
it("refuses a validly rehashed changed quote on every later refresh, retaining the first consent binding", async () => {
  const changed = review(); changed.billing.quote_id = `csq_${"6".repeat(32)}`; changed.payload.billing = structuredClone(changed.billing); changed.payload.pricing = structuredClone(changed.billing);
  changed.review_digest = createHash("sha256").update(canonical(changed.payload)).digest("hex");
  request.mockResolvedValueOnce(Response.json(review())).mockImplementation(() => Promise.resolve(Response.json(changed)));
  show(); await load(); fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Load current review status" }));
  await screen.findByText(/immutable quote changed/); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  await screen.findByText(/immutable quote changed/); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument(); expect(request).toHaveBeenCalledTimes(3);
});
it("a quote expiring while account headers resolve sends no approval or automatic replacement", async () => {
  request.mockResolvedValueOnce(Response.json(review())); show(); await load(); fireEvent.click(screen.getByRole("checkbox"));
  let release!: (value: Record<string, string>) => void;
  vi.mocked(accountMutationHeaders).mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Approve final submission · reserve 3 unified credits" }));
  await waitFor(() => expect(release).toBeTypeOf("function"));
  await act(async () => { now += 60000; release({ "Content-Type": "application/json" }); });
  await screen.findByText(/exact quote expired/); expect(request).toHaveBeenCalledTimes(1); expect(screen.getByRole("checkbox")).not.toBeChecked();
});
it("does not replace the payload under the same immutable quote during a manual status refresh", async () => {
  const changed = review(); changed.payload.disclosures.push("Changed after the first exact review");
  changed.review_digest = createHash("sha256").update(canonical(changed.payload)).digest("hex");
  request.mockResolvedValueOnce(Response.json(review())).mockResolvedValueOnce(Response.json(changed));
  show(); await load(); fireEvent.click(screen.getByRole("button", { name: "Load current review status" }));
  await screen.findByText(/immutable quote changed/); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument(); expect(request).toHaveBeenCalledTimes(2);
});
it("a possible-send terminal response keeps three unified credits held without offering another action", async () => {
  request.mockResolvedValueOnce(Response.json({ ...review(), state: "unknown", payload: {}, submission_status: "unknown" }));
  show(); fireEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  await screen.findByText(/3 unified credits remain held for reconciliation/);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument(); expect(request).toHaveBeenCalledTimes(1);
});
it("accepted preclaim cancellation explains a new application identity without automatically quoting or promising a refund", async () => {
  request.mockResolvedValueOnce(Response.json({ ...review(), state: "approved" }))
    .mockResolvedValueOnce(Response.json({ ...review(), state: "cancelled", payload: {} }));
  show(); await load(); fireEvent.click(screen.getByRole("button", { name: "Cancel future submission actions" }));
  await screen.findByText(/Use a new application ID for another priced attempt/);
  expect(request).toHaveBeenCalledTimes(2);
  expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity: vector.unified5.review_response.id, confirmed: true });
  expect(request.mock.calls[1][0]).toBe("/api/browser-assistance/candidate/cancel");
});
it("an unmount during real payload hashing cannot render or approve a late quote", async () => {
  const original = webcrypto.subtle.digest.bind(webcrypto.subtle);
  let release!: () => void; const paused = new Promise<void>(resolve => { release = resolve; });
  const digest = vi.spyOn(webcrypto.subtle, "digest").mockImplementation(async (...args) => { await paused; return original(...args); });
  request.mockResolvedValueOnce(Response.json(review())); const view = show(); fireEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  await waitFor(() => expect(digest).toHaveBeenCalledTimes(1)); view.unmount(); await act(async () => release());
  expect(request).toHaveBeenCalledTimes(1); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});
