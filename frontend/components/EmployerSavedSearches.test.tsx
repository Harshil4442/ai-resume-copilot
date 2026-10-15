import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EmployerSavedResults, EmployerSearchHistory } from "./EmployerSavedSearches";
import type { EmployerSavedResultPage, EmployerSearchSummary, SearchResult } from "../lib/employerJobs";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("../lib/api", () => ({ apiGet: api.get, apiPostJson: api.post, apiPutJson: api.put }));

const clients: QueryClient[] = [];
function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } } });
  clients.push(client);
  return function TestQueryProvider({ children }: { children: React.ReactNode }) { return <QueryClientProvider client={client}>{children}</QueryClientProvider>; };
}

function summary(id = "search_one", total = 21): EmployerSearchSummary {
  return { id, status: "completed", desired_count: 25, delivered_count: total, reserved_credits: 50,
    charged_credits: 42, refunded_credits: 8, created_at: "2026-10-11T00:00:00Z",
    query: { resume_id: 10, role: id, location: "Bengaluru", remote_only: false, published_within_days: null, desired_count: 25 },
    scope: { candidate_limit_reached: true, known_preference_conflicts: 7 } };
}
function item(number: number): SearchResult {
  return { posting: { id: `job_${number}`, source_id: "synthetic", external_id: String(number), title: `Saved role ${number}`, employer: "Synthetic Employer", location: "Bengaluru", description: "Python ".repeat(20_000), canonical_url: `https://employer.example/jobs/${number}`, apply_url: `https://employer.example/jobs/${number}`, platform: "greenhouse", publication_at: null, last_checked_at: "2026-10-11T00:00:00Z", application_mode: "manual" }, fit: { score: 80, matched_skills: ["Python"], missing_evidence: [], reasons: ["Documented overlap"], scoring_version: "basic-catalog-overlap-v1" }, charged_credits: 2 };
}
function resultPage(saved: EmployerSearchSummary, start: number, total = 21): EmployerSavedResultPage {
  return { search: saved, items: Array.from({ length: Math.min(10, total - start) }, (_, i) => item(start + i + 1)), start_index: start, total_items: total, next_cursor: start + 10 < total ? `signed.result.${start + 10}` : null, snapshot_version: "v1-exact-snapshot" };
}

beforeEach(() => { api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); });
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

describe("free saved search navigation", () => {
  it("loads only a bounded summary page and explicitly selects a search without a paid action", async () => {
    api.get.mockResolvedValue({ items: Array.from({ length: 10 }, (_, index) => summary(`search_${index}`)), next_cursor: "signed.history.10" });
    const select = vi.fn();
    render(<EmployerSearchHistory selectedId={undefined} onSelect={select} />, { wrapper: wrapper() });
    await screen.findByText("search_0");
    expect(screen.getAllByRole("button", { pressed: false })).toHaveLength(10);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/v1/employer-jobs/search-summaries?limit=10");
    expect(screen.getByText(/Opening saved results does not spend/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /search_0/ }));
    expect(select).toHaveBeenCalledWith(summary("search_0"));
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  });

  it("navigates history keyset cursors while bounding each render to ten summaries", async () => {
    const cursor = "signed.history.+/=.10";
    api.get.mockImplementation((url: string) => Promise.resolve(url.includes("cursor=")
      ? { items: [summary("search_10")], next_cursor: null }
      : { items: Array.from({ length: 10 }, (_, index) => summary(`search_${index}`)), next_cursor: cursor }));
    render(<EmployerSearchHistory selectedId={undefined} onSelect={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByText("search_0");
    fireEvent.click(screen.getByRole("button", { name: "Next searches" }));
    await screen.findByText("search_10");
    expect(screen.queryByText("search_0")).not.toBeInTheDocument();
    const url = new URL(api.get.mock.calls.at(-1)![0], "https://hirewiz.example");
    expect(url.searchParams.get("cursor")).toBe(cursor);
    expect(url.searchParams.get("limit")).toBe("10");
    expect(screen.getByRole("button", { name: "Next searches" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous searches" }));
    await screen.findByText("search_0");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("preserves saved order and financial totals on every result page without charging", async () => {
    const saved = summary();
    api.get.mockImplementation((url: string) => {
      const cursor = new URL(url, "https://hirewiz.example").searchParams.get("cursor");
      return Promise.resolve(resultPage(saved, cursor ? Number(cursor.split(".").at(-1)) : 0));
    });
    render(<EmployerSavedResults search={saved} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByText("Showing 1–10 of 21 saved jobs");
    expect(screen.getAllByRole("article")).toHaveLength(10);
    expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(Array.from({ length: 10 }, (_, index) => `Saved role ${index + 1}`));
    fireEvent.click(screen.getByRole("button", { name: "Next results" }));
    await screen.findByText("Showing 11–20 of 21 saved jobs");
    expect(screen.queryByRole("heading", { name: "Saved role 1" })).not.toBeInTheDocument();
    expect(screen.getByText("42 service credits charged · 8 returned")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next results" }));
    await screen.findByText("Showing 21–21 of 21 saved jobs");
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Next results" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous results" }));
    await screen.findByText("Showing 11–20 of 21 saved jobs");
    expect(api.get.mock.calls.every(([url]) => String(url).startsWith("/v1/employer-jobs/searches/search_one/results?limit=10"))).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  });

  it("keeps candidate-cap and source-scope limits honest and prepares only the selected visible job", async () => {
    const prepare = vi.fn();
    api.get.mockResolvedValue(resultPage(summary(), 0));
    render(<EmployerSavedResults search={summary()} preparing={false} canPrepare onPrepare={prepare} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    expect(screen.getByText(/Only the first 1,000 current candidates/)).toBeInTheDocument();
    expect(screen.getByText(/They do not find new jobs or spend credits/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[2]);
    expect(prepare).toHaveBeenCalledOnce();
    expect(prepare).toHaveBeenCalledWith("job_3");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("shows pending state without stale cards or an empty-results claim when another search is selected", async () => {
    let finish!: (page: EmployerSavedResultPage) => void;
    api.get.mockResolvedValueOnce(resultPage(summary("old_search"), 0)).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const view = render(<EmployerSavedResults key="old_search" search={summary("old_search")} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    view.rerender(<EmployerSavedResults key="next_search" search={summary("next_search")} preparing={false} canPrepare onPrepare={vi.fn()} />);
    expect(screen.getByRole("status", { name: "Loading saved results" })).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.queryByText("This search saved no jobs.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next results" })).toBeDisabled();
    await act(async () => { finish({ ...resultPage(summary("next_search"), 0), items: [{ ...item(1), posting: { ...item(1).posting, title: "Next search role" } }] }); });
    expect(await screen.findByRole("heading", { name: "Next search role" })).toBeInTheDocument();
  });

  it("retries a failed free read without manufacturing an empty result or a paid request", async () => {
    api.get.mockRejectedValueOnce(new Error("Synthetic read unavailable")).mockResolvedValueOnce(resultPage(summary(), 0));
    render(<EmployerSavedResults search={summary()} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("alert");
    expect(screen.queryByText("This search saved no jobs.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry saved results" }));
    await screen.findByRole("heading", { name: "Saved role 1" });
    expect(api.get.mock.calls[0][0]).toBe(api.get.mock.calls[1][0]);
    expect(api.post).not.toHaveBeenCalled();
  });

  it("keeps a malformed legacy fit entry in its saved position without crashing or starting paid work", async () => {
    const malformed = { ...item(1), fit: { score: 80, reasons: null } } as unknown as SearchResult;
    api.get.mockResolvedValue({ ...resultPage(summary(), 0), items: [malformed], total_items: 1, next_cursor: null });
    render(<EmployerSavedResults search={summary()} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved job details need review" });
    expect(screen.getByText("Showing 1–1 of 1 saved jobs")).toBeInTheDocument();
    expect(screen.getByText("42 service credits charged · 8 returned")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare application" })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("does not enable application preparation without a resume or during existing preparation", async () => {
    api.get.mockResolvedValue(resultPage(summary(), 0));
    const view = render(<EmployerSavedResults search={summary()} preparing={false} canPrepare={false} onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    expect(screen.getAllByRole("button", { name: "Prepare application" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
    view.rerender(<EmployerSavedResults search={summary()} preparing canPrepare onPrepare={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: "Prepare application" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("refreshes history summaries only, leaving saved totals and result choice intact", async () => {
    api.get.mockImplementation((url: string) => Promise.resolve(url.includes("search-summaries") ? { items: [summary()], next_cursor: null } : resultPage(summary(), 0)));
    render(<><EmployerSearchHistory selectedId="search_one" onSelect={vi.fn()} /><EmployerSavedResults search={summary()} preparing={false} canPrepare onPrepare={vi.fn()} /></>, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    await screen.findByText("search_one");
    const resultReads = api.get.mock.calls.filter(([url]) => String(url).includes("/results?")).length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    await waitFor(() => expect(api.get.mock.calls.filter(([url]) => String(url).includes("search-summaries")).length).toBe(2));
    expect(api.get.mock.calls.filter(([url]) => String(url).includes("/results?")).length).toBe(resultReads);
    expect(screen.getByText("42 service credits charged · 8 returned")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /search_one/ })).toHaveAttribute("aria-pressed", "true");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("preserves malformed nested history metadata for free selection while showing static guidance", async () => {
    const saved = summary();
    (saved.query as unknown as Record<string, unknown>).role = { sentinel: "do not render raw saved metadata" };
    api.get.mockResolvedValue({ items: [saved], next_cursor: null });
    const select = vi.fn();
    render(<EmployerSearchHistory selectedId={undefined} onSelect={select} />, { wrapper: wrapper() });
    const entry = await screen.findByRole("button", { name: /Saved search.*21 jobs/ });
    expect(screen.getByText("Saved search choices need review.")).toBeInTheDocument();
    expect(screen.queryByText(/do not render raw saved metadata/)).not.toBeInTheDocument();
    fireEvent.click(entry);
    expect(select).toHaveBeenCalledWith(saved);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  });

  it.each(["unit_price", "requested_count", "maximum_credits"] as const)("does not render or coerce a nested saved quote %s", async (field) => {
    const saved = summary();
    saved.scope!.price_quote = { unit_price: 2, requested_count: 25, maximum_credits: 50, pricing_version: "saved-v1", search_input_fingerprint: "original" };
    (saved.scope!.price_quote as unknown as Record<string, unknown>)[field] = { sentinel: "raw quote leaf" };
    api.get.mockResolvedValue(resultPage(saved, 0));
    render(<EmployerSavedResults search={saved} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    expect(screen.getByText("Original price quote needs review. Saved charges and returned credits are shown above.")).toBeInTheDocument();
    expect(screen.queryByText(/Saved quote:/)).not.toBeInTheDocument();
    expect(screen.getByText("42 service credits charged · 8 returned")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–10 of 21 saved jobs")).toBeInTheDocument();
    expect(screen.queryByText(/raw quote leaf/)).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  });

  it("keeps string prices as review metadata while displaying valid original zero quotes exactly", async () => {
    const saved = summary();
    saved.scope!.price_quote = { unit_price: 2, requested_count: 25, maximum_credits: 50, pricing_version: "saved-v1", search_input_fingerprint: "original" };
    (saved.scope!.price_quote as unknown as Record<string, unknown>).unit_price = "2";
    api.get.mockResolvedValue(resultPage(saved, 0));
    const view = render(<EmployerSavedResults key="bad" search={saved} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByText(/Original price quote needs review/);
    expect(screen.queryByText(/Saved quote:/)).not.toBeInTheDocument();
    const valid = { ...saved, id: "original_zero", scope: { ...saved.scope, price_quote: { ...saved.scope!.price_quote!, unit_price: 0, maximum_credits: 0 } } };
    api.get.mockResolvedValue(resultPage(valid, 0));
    view.rerender(<EmployerSavedResults key="good" search={valid} preparing={false} canPrepare onPrepare={vi.fn()} />);
    expect(await screen.findByText("Saved quote: 0 per new job · up to 0 service credits for 25 requested jobs.")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("handles nested preference counters and does not treat a nested candidate flag as true", async () => {
    const saved = summary();
    const scope = saved.scope as unknown as Record<string, unknown>;
    scope.known_preference_conflicts = { sentinel: "raw counter" };
    scope.candidate_limit_reached = { sentinel: "raw flag" };
    api.get.mockResolvedValue(resultPage(saved, 0));
    render(<EmployerSavedResults search={saved} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    expect(screen.getByText(/Saved preference metadata needs review/)).toBeInTheDocument();
    expect(screen.queryByText(/Only the first 1,000/)).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it.each([{}, { id: "" }, { id: { sentinel: "nested identity" } }])("keeps an API-readable malformed posting dictionary in its position before accessing identity", async (posting) => {
    const malformed = { ...item(1), posting } as unknown as SearchResult;
    api.get.mockResolvedValue({ ...resultPage(summary(), 0), items: [malformed], total_items: 1, next_cursor: null });
    render(<EmployerSavedResults search={summary()} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved job details need review" });
    expect(screen.getByText("Showing 1–1 of 1 saved jobs")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare application" })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("does not render invalid nested job-preference states as matching employer facts", async () => {
    const invalid = { ...item(1), preference_evaluation: { states: ["match"], messages: [{ sentinel: "nested advice" }] } } as unknown as SearchResult;
    api.get.mockResolvedValue({ ...resultPage(summary(), 0), items: [invalid], total_items: 1, next_cursor: null });
    render(<EmployerSavedResults search={summary()} preparing={false} canPrepare onPrepare={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Saved role 1" });
    expect(screen.getByText(/Saved job preferences need review/)).toBeInTheDocument();
    expect(screen.queryByText(/Employer facts match these choices/)).not.toBeInTheDocument();
    expect(screen.getByText("42 service credits charged · 8 returned")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });
});
