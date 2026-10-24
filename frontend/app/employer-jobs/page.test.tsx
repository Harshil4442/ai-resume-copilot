import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ owner: "one", session: { status: "authenticated", data: { user: { id: "one" } } } as { status: string; data: { user: { id: string } } | null }, get: vi.fn(), post: vi.fn() }));
vi.mock("../../lib/api", () => ({ apiGet: state.get, apiPostJson: state.post, ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
vi.mock("next-auth/react", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children, useSession: () => state.session }));
vi.mock("next/navigation", () => ({ usePathname: () => "/employer-jobs" }));
vi.mock("next/dynamic", () => ({ default: () => ({ id }: { id: string }) => <div data-testid="application-panel">Review application {id}</div> }));

import EmployerJobsPage from "./page";
import { ApiError } from "../../lib/api";
import SessionProviderWrapper from "../../components/SessionProviderWrapper";
import type { EmployerSearchPreparation, EmployerSearchSummary, SearchResult } from "../../lib/employerJobs";

let client: QueryClient;
function summary(owner = state.owner): EmployerSearchSummary {
  return { id: `search_${owner}`, status: "completed", desired_count: 3, delivered_count: 3, reserved_credits: 6, charged_credits: 4, refunded_credits: 2, created_at: "2026-10-11T00:00:00Z", query: { resume_id: owner === "one" ? 10 : 20, role: `Python Engineer ${owner}`, location: "Bengaluru", remote_only: true, published_within_days: 7, desired_count: 3, excluded_employers: ["Excluded Employer"] }, scope: { candidate_limit_reached: true, price_quote: { unit_price: 2, pricing_version: "original-price", requested_count: 3, maximum_credits: 6, search_input_fingerprint: "exact-original-input" } } };
}
function item(owner = state.owner): SearchResult {
  return { posting: { id: `job_${owner}`, source_id: "source_fixture", external_id: "1", title: `Saved job for ${owner}`, employer: "Synthetic Employer", location: "Bengaluru", description: "Python", canonical_url: "https://employer.example/jobs/1", apply_url: "https://employer.example/jobs/1", platform: "greenhouse", publication_at: null, last_checked_at: "2026-10-11T00:00:00Z", application_mode: "manual" }, fit: { score: 80, matched_skills: ["Python"], missing_evidence: [], reasons: ["Documented overlap"], scoring_version: "basic-catalog-overlap-v1" }, charged_credits: 2 };
}
function read(url: string) {
  const owner = state.owner;
  if (url.endsWith("/catalog")) return Promise.resolve({ enabled: true, auto_submit_enabled: false, balance: 50, search_credits_per_job: 9, apply_credits_per_job: 20, pricing_version: "current-price", max_search_jobs: 100, sources: [] });
  if (url === "/resume/list") return Promise.resolve({ resumes: [{ id: owner === "one" ? 10 : 20, filename: `original-${owner}.pdf`, source_available: true }] });
  if (url.endsWith("/applications")) return Promise.resolve({ items: [] });
  if (url.includes("/search-summaries?")) return Promise.resolve({ items: [summary(owner)], next_cursor: null });
  if (url.includes("/results?")) return Promise.resolve({ search: summary(owner), items: [item(owner)], total_items: 1, start_index: 0, next_cursor: null, snapshot_version: "v1-exact-snapshot" });
  throw new Error(`Unexpected GET in local control: ${url}`);
}
function pendingSearch(): EmployerSearchPreparation {
  return { id: "search_pending", kind: "search_preparation", status: "pending", as_of: "2026-10-11T00:00:00Z", desired_count: 3, accepted_quote: { unit_price: 2, pricing_version: "accepted-price", requested_count: 3, maximum_credits: 6, search_input_fingerprint: "accepted-input" }, candidates_examined: 0, charged_credits: 0, reserved_credits: 6, refunded_credits: 0, completed_search_id: null, failure_reason: null };
}
function withClient() {
  return function TestQueryProvider({ children }: { children: React.ReactNode }) { return <QueryClientProvider client={client}>{children}</QueryClientProvider>; };
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } } });
  state.owner = "one"; state.session = { status: "authenticated", data: { user: { id: "one" } } };
  state.get.mockReset().mockImplementation(read); state.post.mockReset();
});
afterEach(() => { cleanup(); client.clear(); });

describe("employer page saved-search composition", () => {
  it("refreshes a changed price without posting again until another explicit click", async () => {
    let catalogReads = 0;
    state.get.mockImplementation((url: string) => url.endsWith("/catalog") ? Promise.resolve({ enabled: true, auto_submit_enabled: false, balance: 50, search_credits_per_job: ++catalogReads === 1 ? 9 : 10, apply_credits_per_job: 20, pricing_version: catalogReads === 1 ? "current-price" : "renewed-price", max_search_jobs: 100, sources: [] }) : read(url));
    state.post.mockRejectedValueOnce(new ApiError("The search price changed. Refresh and review it.", 409, { detail: { code: "search_price_requires_review" } })).mockResolvedValueOnce({ ...summary(), items: [item()] });
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await screen.findByText("Up to 30 service credits");
    await screen.findByRole("alert");
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][1].accepted_quote).toEqual({ pricing_version: "current-price", unit_price: 9, maximum_credits: 27 });
    expect(screen.getByText(/Review the refreshed maximum above and click Find jobs again/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Find jobs" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await waitFor(() => expect(state.post).toHaveBeenCalledTimes(2));
    expect(state.post.mock.calls[1][1].accepted_quote).toEqual({ pricing_version: "renewed-price", unit_price: 10, maximum_credits: 30 });
    expect(state.post.mock.calls[1][1].idempotency_key).not.toBe(state.post.mock.calls[0][1].idempotency_key);
  });

  it("shows the funded pending quote and cancels without applying or charging again", async () => {
    state.post.mockImplementation((url: string) => Promise.resolve(url.endsWith("/cancel") ? { ...pendingSearch(), status: "cancelled", reserved_credits: 0, refunded_credits: 6 } : pendingSearch()));
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await screen.findByRole("heading", { name: "Preparing your search" });
    expect(screen.getByText(/6 service credits held · 0 returned/)).toBeInTheDocument();
    expect(screen.getByText("No service credits charged")).toBeInTheDocument();
    expect(screen.getByText(/Accepted quote: 2 per new job · up to 6 service credits/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Finding employer jobs…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel search" }));
    await screen.findByText(/0 service credits held · 6 returned/);
    expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/employer-jobs/searches", "/v1/employer-jobs/search-preparations/search_pending/cancel"]);
    expect(screen.queryByRole("button", { name: "Prepare application" })).not.toBeInTheDocument();
  });

  it("keeps the latest completed selection when an older free progress read finishes late", async () => {
    const pending = { ...summary(), id: "search_pending", status: "preparing", delivered_count: 0, charged_credits: 0, refunded_credits: 0, query: { ...summary().query, role: "Pending search role" } };
    let resolveProgress!: (value: EmployerSearchPreparation) => void;
    state.get.mockImplementation((url: string) => url.includes("search-summaries?")
      ? Promise.resolve({ items: [pending, summary()], next_cursor: null })
      : url.endsWith("/search-preparations/search_pending") ? new Promise<EmployerSearchPreparation>((resolve) => { resolveProgress = resolve; }) : read(url));
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Pending search role");
    fireEvent.click(screen.getByRole("button", { name: /Pending search role/ }));
    await screen.findByRole("status", { name: "Opening search progress" });
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    await act(async () => { resolveProgress(pendingSearch()); });
    expect(screen.getByRole("heading", { name: "Saved job for one" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Preparing your search" })).not.toBeInTheDocument();
    expect(state.post).not.toHaveBeenCalled();
  });

  it("loads summaries, restores the selected query, and shows its original quote despite current price changes", async () => {
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    expect(state.get.mock.calls.some(([url]) => url === "/v1/employer-jobs/searches")).toBe(false);
    expect(state.get.mock.calls.some(([url]) => String(url).includes("/results?"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    expect(screen.getByLabelText("Resume")).toHaveValue("10");
    expect(screen.getByLabelText("Target role")).toHaveValue("Python Engineer one");
    expect(screen.getByLabelText("Location")).toHaveValue("Bengaluru");
    expect(screen.getByLabelText("Jobs to find")).toHaveValue(3);
    expect(screen.getByLabelText("Published/released within")).toHaveValue("7");
    expect(screen.getByLabelText("Remote roles only")).toBeChecked();
    expect(screen.getByText("4 service credits charged · 2 returned")).toBeInTheDocument();
    expect(screen.getByText("Saved quote: 2 per new job · up to 6 service credits for 3 requested jobs.")).toBeInTheDocument();
    expect(screen.getByText("Up to 27 service credits")).toBeInTheDocument();
    expect(state.post).not.toHaveBeenCalled();
  });

  it("retains explicit original-resume preparation for the selected job and opens the existing review flow", async () => {
    state.post.mockResolvedValue({ id: "application_saved", status: "manual_handoff" });
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    fireEvent.click(screen.getByRole("button", { name: "Prepare application" }));
    await screen.findByTestId("application-panel");
    expect(state.post).toHaveBeenCalledTimes(1);
    const [url, payload] = state.post.mock.calls[0];
    expect(url).toBe("/v1/employer-jobs/applications");
    expect(payload).toMatchObject({ posting_id: "job_one", resume_id: 10, resume_choice: "original" });
    expect(payload.idempotency_key).toEqual(expect.any(String));
    expect(state.post.mock.calls.some(([target]) => String(target).includes("tailor") || String(target).includes("execute") || String(target).includes("approve"))).toBe(false);
  });

  it("keeps requested count, excluded employers, and idempotency for an interrupted explicit paid search", async () => {
    state.post.mockRejectedValueOnce(new Error("Synthetic interrupted search")).mockResolvedValueOnce({ ...summary(), items: [item()] });
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await waitFor(() => expect(state.post).toHaveBeenCalledTimes(2));
    const [first, retry] = state.post.mock.calls;
    expect(first[0]).toBe("/v1/employer-jobs/searches");
    expect(first[1]).toMatchObject({ desired_count: 3, resume_id: 10, excluded_employers: ["Excluded Employer"], role: "Python Engineer one" });
    expect(retry[1]).toEqual(first[1]);
    expect(first[1].idempotency_key).toEqual(expect.any(String));
  });

  it("shows and retries the originally accepted maximum after a lost response and a lower current price", async () => {
    let catalogReads = 0;
    state.get.mockImplementation((url: string) => url.endsWith("/catalog") ? Promise.resolve({ enabled: true, auto_submit_enabled: false, balance: 50, search_credits_per_job: ++catalogReads === 1 ? 9 : 1, apply_credits_per_job: 20, pricing_version: catalogReads === 1 ? "current-price" : "lower-price", max_search_jobs: 100, sources: [] }) : read(url));
    state.post.mockRejectedValueOnce(new Error("Synthetic lost response")).mockResolvedValueOnce({ ...summary(), items: [item()] });
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await screen.findByRole("alert");
    await waitFor(() => expect(catalogReads).toBeGreaterThan(1));
    expect(screen.getByText("Up to 27 service credits")).toBeInTheDocument();
    expect(screen.queryByText("Up to 3 service credits")).not.toBeInTheDocument();
    expect(screen.getByText(/Checking your existing request at its originally accepted price/)).toBeInTheDocument();
    expect(state.post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await waitFor(() => expect(state.post).toHaveBeenCalledTimes(2));
    expect(state.post.mock.calls[1][1]).toEqual(state.post.mock.calls[0][1]);
    expect(state.post.mock.calls[1][1].accepted_quote).toEqual({ pricing_version: "current-price", unit_price: 9, maximum_credits: 27 });
  });

  it("remounts summary/result caches and local selection before another owner renders, including late old-owner reads", async () => {
    const view = render(<SessionProviderWrapper><EmployerJobsPage /></SessionProviderWrapper>);
    await screen.findByText("Python Engineer one");
    let completeOldRead!: (value: unknown) => void;
    state.get.mockImplementation((url: string) => url.includes("/searches/search_one/results?") ? new Promise((resolve) => { completeOldRead = resolve; }) : read(url));
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("status", { name: "Loading saved results" });
    state.owner = "two"; state.session = { status: "authenticated", data: { user: { id: "two" } } };
    view.rerender(<SessionProviderWrapper><EmployerJobsPage /></SessionProviderWrapper>);
    expect(screen.queryByText("Python Engineer one")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Resume")).toHaveValue("");
    expect(screen.getByLabelText("Target role")).toHaveValue("");
    await screen.findByText("Python Engineer two");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer two/ }));
    await screen.findByRole("heading", { name: "Saved job for two" });
    await act(async () => { completeOldRead({ search: summary("one"), items: [item("one")], total_items: 1, start_index: 0, next_cursor: null, snapshot_version: "old" }); });
    expect(screen.queryByRole("heading", { name: "Saved job for one" })).not.toBeInTheDocument();
    expect(screen.queryByText("Python Engineer one")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Resume")).toHaveValue("20");
    expect(state.post).not.toHaveBeenCalled();
  });

  it.each([
    ["role", { sentinel: "nested role" }], ["resume_id", { sentinel: "nested resume" }],
    ["location", { sentinel: "nested location" }], ["remote_only", { sentinel: "nested remote flag" }],
    ["published_within_days", { sentinel: "nested date choice" }], ["desired_count", { sentinel: "nested count" }],
    ["excluded_employers", [{ sentinel: "nested employer" }]],
    ["preferences", { version: 1, country_codes: { sentinel: "nested country" } }],
    ["preferences", { version: 1, unknown_metadata: "include", country_codes: null, posting_languages: null, authorized_country_codes: null, employment_types: null, sponsorship_required: null, willing_to_relocate: null, salary: { minimum: { sentinel: "nested salary" }, maximum: null, currency: "INR", period: "year" } }],
  ])("opens saved jobs safely without restoring malformed %s into form state", async (field, value) => {
    const saved = summary();
    (saved.query as unknown as Record<string, unknown>)[field as string] = value;
    state.get.mockImplementation((url: string) => url.includes("search-summaries?")
      ? Promise.resolve({ items: [saved], next_cursor: null })
      : url.includes("/results?") ? Promise.resolve({ search: saved, items: [item()], total_items: 1, start_index: 0, next_cursor: null, snapshot_version: "saved-v1" }) : read(url));
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Saved search choices need review.");
    fireEvent.click(screen.getByRole("button", { pressed: false }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    expect(screen.getByLabelText("Resume")).toHaveValue("");
    expect(screen.getByLabelText("Target role")).toHaveValue("");
    expect(screen.getByLabelText("Location")).toHaveValue("");
    expect(screen.getByLabelText("Jobs to find")).toHaveValue(null);
    expect(screen.getByText("4 service credits charged · 2 returned")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare application" })).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
  });

  it("allows an explicit new resume choice after a damaged saved query, with no implicit application or repricing", async () => {
    const saved = summary();
    (saved.query as unknown as Record<string, unknown>).resume_id = { sentinel: "unusable saved resume" };
    state.get.mockImplementation((url: string) => url.includes("search-summaries?")
      ? Promise.resolve({ items: [saved], next_cursor: null })
      : url.includes("/results?") ? Promise.resolve({ search: saved, items: [item()], total_items: 1, start_index: 0, next_cursor: null, snapshot_version: "saved-v1" }) : read(url));
    state.post.mockResolvedValue({ id: "explicit_application", status: "manual_handoff" });
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Saved search choices need review.");
    fireEvent.click(screen.getByRole("button", { pressed: false }));
    await screen.findByRole("heading", { name: "Saved job for one" });
    expect(state.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Resume"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Prepare application" }));
    await screen.findByTestId("application-panel");
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][0]).toBe("/v1/employer-jobs/applications");
    expect(state.post.mock.calls[0][1]).toMatchObject({ posting_id: "job_one", resume_id: 10, resume_choice: "original" });
  });

  it("clears a prior valid resume and draft before opening a different damaged saved search", async () => {
    const valid = summary();
    const damaged = { ...summary(), id: "damaged_second" };
    (damaged.query as unknown as Record<string, unknown>).role = { sentinel: "nested second query" };
    state.get.mockImplementation((url: string) => url.includes("search-summaries?")
      ? Promise.resolve({ items: [valid, damaged], next_cursor: null })
      : url.includes("/results?") ? Promise.resolve({ search: url.includes("damaged_second") ? damaged : valid, items: [{ ...item(), posting: { ...item().posting, title: url.includes("damaged_second") ? "Second saved job" : "First saved job" } }], total_items: 1, start_index: 0, next_cursor: null, snapshot_version: "saved-v1" }) : read(url));
    render(<EmployerJobsPage />, { wrapper: withClient() });
    await screen.findByText("Python Engineer one");
    fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
    await screen.findByRole("heading", { name: "First saved job" });
    expect(screen.getByLabelText("Resume")).toHaveValue("10");
    fireEvent.click(screen.getByRole("button", { name: /Saved search.*3 jobs/ }));
    await screen.findByRole("heading", { name: "Second saved job" });
    expect(screen.queryByRole("heading", { name: "First saved job" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Resume")).toHaveValue("");
    expect(screen.getByLabelText("Target role")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Prepare application" })).toBeDisabled();
    expect(state.post).not.toHaveBeenCalled();
  });
});

it.each([429, 503])("pauses hidden search polling and requires an explicit status read after HTTP%s", async (status) => {
  let visible: DocumentVisibilityState = "visible";
  const visibility = vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visible);
  let progressReads = 0;
  state.get.mockImplementation((url: string) => url.endsWith("/search-preparations/search_pending")
    ? ++progressReads === 1 ? Promise.reject(new ApiError("Status unavailable", status, {})) : Promise.resolve(pendingSearch())
    : read(url));
  state.post.mockResolvedValue(pendingSearch());
  render(<EmployerJobsPage />, { wrapper: withClient() });
  await screen.findByText("Python Engineer one");
  fireEvent.click(screen.getByRole("button", { name: /Python Engineer one/ }));
  await screen.findByRole("heading", { name: "Saved job for one" });
  vi.useFakeTimers();
  const tick = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
  try {
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" })); await tick(1);
    expect(screen.getByRole("heading", { name: "Preparing your search" })).toBeInTheDocument();
    visible = "hidden"; await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await tick(120000); expect(progressReads).toBe(0);
    visible = "visible"; await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await tick(5000); expect(progressReads).toBe(1);
    await tick(120000); expect(progressReads).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry search status" })); await tick(1);
    expect(progressReads).toBe(2); expect(state.post).toHaveBeenCalledTimes(1);
  } finally { visibility.mockRestore(); vi.useRealTimers(); }
});
