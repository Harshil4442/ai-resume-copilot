import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ResumeListResponse } from "../lib/types";
import type { EmployerSearchSummary } from "../lib/employerJobs";
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../lib/api", () => ({ apiGet: api.get, apiPostJson: api.post,
  ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
import { ApiError } from "../lib/api";
import { UnifiedDiscoveryPanel } from "./UnifiedDiscoveryPanel";
const quote = { schema_version: "unified-service-quote-v1", quote_id: "csq_search", operation: "job_discovery_batch", input_fingerprint: "d".repeat(64),
  route_id: "discovery-v1", route_version: "1", mode: "native", tariff_version: "v1", credits: 1, deliverable: "Up to 5 new verified employer jobs.", supported_limits: { maximum_results: 5 },
  created_at: "2026-10-28T10:00:00Z", expires_at: "2026-10-28T10:15:00Z", server_time: "2026-10-28T10:00:00Z" };
const resumes = [{ id: 1, filename: "original.pdf" }] as ResumeListResponse["resumes"];
const resultItem = { posting: { id: "job_one", title: "Engineer", employer: "Example", location: "India", publication_at: null, canonical_url: "https://example.com/careers/one" }, fit: { score: 80, reasons: ["Skill overlap"], matched_skills: ["Python"], missing_evidence: [] }, charged_credits: 0 };
function batch(count = 2) { return { id: "search_one", status: "completed", billing_currency: "credits", desired_count: 5, delivered_count: count,
  charged_credits: Number(count > 0), reserved_credits: 0, refunded_credits: Number(count === 0), partial_batch: count > 0 && count < 5,
  maximum_batch_size: 5, scope: {}, items: Array.from({ length: count }, () => resultItem) }; }
let client: QueryClient;
beforeEach(() => { client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  api.get.mockReset().mockResolvedValue(batch()); api.post.mockReset().mockImplementation(async (url) => {
    if (url.endsWith("/discovery-quotes")) return quote;
    if (url.endsWith("/discovery-batches")) return batch();
    throw new Error(`Unexpected ${url}`);
  }); });
afterEach(() => { cleanup(); client.clear(); });
function view(extra: Partial<React.ComponentProps<typeof UnifiedDiscoveryPanel>> = {}) {
  return render(<QueryClientProvider client={client}><UnifiedDiscoveryPanel resumes={resumes} enabled preparingApplication={false} onPrepare={vi.fn()} onResume={vi.fn()} onLockChange={vi.fn()} {...extra} /></QueryClientProvider>);
}
async function review() {
  fireEvent.change(screen.getByRole("combobox", { name: "Resume" }), { target: { value: "1" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Target role" }), { target: { value: "Engineer" } });
  fireEvent.click(screen.getByRole("button", { name: "Review one-credit search" }));
  return screen.findByRole("button", { name: "Approve 1 credit" });
}
it("discloses partial results and only creates one bounded batch after approval", async () => {
  view(); const approve = await review(); expect(api.post).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/A completed batch with 1–4 new verified jobs still costs one credit/)).toBeVisible();
  fireEvent.click(approve); await screen.findByText("Partial batch: 2 of 5 requested jobs. The reviewed one-credit batch charge applies.");
  expect(screen.getByText("1 credit charged · 0 held · 0 restored")).toBeVisible();
  expect(api.post.mock.calls.filter(([url]) => url.endsWith("/discovery-batches"))).toEqual([["/v1/credits/discovery-batches", expect.objectContaining({ desired_count: 5, resume_id: 1, accepted_service_quote_id: "csq_search", idempotency_key: expect.any(String) })]]);
});
it("keeps empty delivery free and never silently requests another batch", async () => {
  api.get.mockResolvedValue(batch(0)); api.post.mockImplementation(async (url) => url.endsWith("/discovery-quotes") ? quote : batch(0));
  view(); fireEvent.click(await review()); await screen.findByText("0 credits charged · 0 held · 1 restored");
  expect(screen.getByText(/No new qualifying jobs were found/)).toBeVisible(); expect(api.post).toHaveBeenCalledTimes(2);
});
it("recovers an uncertain create with exactly the original body and key", async () => {
  let attempts = 0; api.post.mockImplementation(async (url) => { if (url.endsWith("/discovery-quotes")) return quote; if (++attempts === 1) throw new TypeError("Response lost"); return batch(); });
  const lock = vi.fn(); view({ onLockChange: lock }); fireEvent.click(await review());
  fireEvent.click(await screen.findByRole("button", { name: "Check approved search" })); await screen.findByText("1 credit charged · 0 held · 0 restored");
  const calls = api.post.mock.calls.filter(([url]) => url.endsWith("/discovery-batches")); expect(calls).toHaveLength(2); expect(calls[1][1]).toEqual(calls[0][1]);
  expect(api.post.mock.calls.filter(([url]) => url.endsWith("/discovery-quotes"))).toHaveLength(1); expect(lock).toHaveBeenCalledWith(true);
});
it("permits a fresh quote only after a definitive pre-work rejection", async () => {
  api.post.mockImplementation(async (url) => { if (url.endsWith("/discovery-quotes")) return quote; throw new ApiError("Quote expired", 409, { detail: { code: "credit_service_quote_rejected", run_created: false } }); });
  view(); fireEvent.click(await review()); await screen.findByText("No search was created. Review a fresh quote when ready.");
  expect(screen.getByRole("button", { name: "Review one-credit search" })).toBeEnabled(); expect(screen.queryByRole("button", { name: "Check approved search" })).toBeNull();
});
it("requires a fresh review when the chosen role changes", async () => {
  view(); await review(); fireEvent.change(screen.getByRole("textbox", { name: "Target role" }), { target: { value: "Designer" } });
  expect(screen.getByRole("button", { name: "Approve 1 credit" })).toBeDisabled(); expect(api.post).toHaveBeenCalledTimes(1);
});
it("opens saved results through a read even when new execution is unavailable", async () => {
  view({ enabled: false, initialSearch: { id: "search_one", query: { resume_id: 1, role: "Engineer", location: "", remote_only: false, published_within_days: null } } as EmployerSearchSummary });
  await screen.findByText("1 credit charged · 0 held · 0 restored"); expect(api.post).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "Review one-credit search" })).toBeDisabled();
});
it("cancels only the selected preparing batch and shows the restored credit", async () => {
  const preparing = { ...batch(0), status: "preparing", reserved_credits: 1, refunded_credits: 0 }; api.get.mockResolvedValue(preparing);
  api.post.mockImplementation(async (url) => url.endsWith("/discovery-quotes") ? quote : url.endsWith("/cancel") ? { ...batch(0), status: "cancelled" } : preparing);
  view(); fireEvent.click(await review()); fireEvent.click(await screen.findByRole("button", { name: "Cancel search" })); await screen.findByText("Search cancelled");
  expect(api.post.mock.calls.at(-1)?.[0]).toBe("/v1/credits/discovery-batches/search_one/cancel"); expect(screen.getByText("0 credits charged · 0 held · 1 restored")).toBeVisible();
});
it("rejects a quote that expands the approved batch or increases the price", async () => {
  api.post.mockResolvedValue({ ...quote, credits: 2 }); view(); await review().catch(() => null);
  await screen.findByText("A verified one-credit search quote is unavailable. No batch has been approved."); expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull(); expect(api.post).toHaveBeenCalledTimes(1);
});

it("prepares a selected result with this form's exact resume choice", async () => {
  const prepare = vi.fn(); view({ onPrepare: prepare }); fireEvent.click(await review()); await screen.findByText("1 credit charged · 0 held · 0 restored");
  fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]); expect(prepare).toHaveBeenCalledExactlyOnceWith("job_one", 1);
});
