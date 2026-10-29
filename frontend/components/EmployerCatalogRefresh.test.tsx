import { ownedBootstrapFixture } from "../lib/fixtures/ownedBootstrap";
import React, { Suspense, lazy } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { EmployerApplicationBatch, JobServiceCatalog } from "../lib/employerJobs";

const state = vi.hoisted(() => ({ pathname: "/employer-jobs" }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: "authenticated", data: { user: { id: "one" } } }) }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useParams: () => ({ id: "batch_one" }), useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/dynamic", () => ({ default: (loader: Parameters<typeof lazy>[0]) => {
  const Component = lazy(loader);
  return function DynamicComponent(props: Record<string, unknown>) {
    return <Suspense fallback={<p>Loading application review</p>}><Component {...props} /></Suspense>;
  };
} }));

import EmployerJobsPage from "../app/employer-jobs/page";
import EmployerBatchPage from "../app/employer-jobs/batches/[id]/page";
import Nav from "./Nav";

const catalogPath = "/api/backend/v1/employer-jobs/catalog";
const applicationsKey = ["employer-jobs", "applications"];
const limits = { version: "synthetic", daily_limit: 10, rolling_limit: 20, rolling_days: 7,
  pending_limit: 5, daily_credit_limit: 100, rolling_credit_limit: 200, batch_limit: 10,
  batch_credit_limit: 100, quote_hours: 24, day_boundary: "UTC", counting_policy: "synthetic" };
const catalog: JobServiceCatalog = { enabled: true, auto_submit_enabled: false, balance: 50,
  search_credits_per_job: 2, apply_credits_per_job: 20, pricing_version: "synthetic", max_search_jobs: 10,
  admission_limits: limits, sources: [] };
const batch: EmployerApplicationBatch = { id: "batch_one", status: "quoted", package_digest: "c".repeat(64),
  items: [], admission_snapshot: limits, quoted_credits: 0, max_total_credits: 0,
  created_at: "2026-10-29T00:00:00Z", expires_at: "2099-10-30T00:00:00Z",
  approved_at: null, cancelled_at: null, application_statuses: {} };
type PendingRead = { signal: AbortSignal; resolve: (value: Response) => void };
let client: QueryClient;
let requests: ReturnType<typeof vi.fn>;
let catalogReads: PendingRead[];
let abortedCatalogReads: number;

function json(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false }, mutations: { retry: false } } });
  state.pathname = "/employer-jobs";
  catalogReads = []; abortedCatalogReads = 0;
  requests = vi.fn((path: string, init: RequestInit = {}) => {
    if (path === catalogPath) {
      if (!catalogReads.length && client.getQueryData(["employer-jobs", "catalog"]) === undefined) {
        return Promise.resolve(json(catalog));
      }
      return new Promise<Response>((resolve, reject) => {
        const signal = init.signal as AbortSignal;
        catalogReads.push({ signal, resolve });
        signal.addEventListener("abort", () => { abortedCatalogReads += 1; reject(new DOMException("Aborted", "AbortError")); }, { once: true });
      });
    }
    if (path === "/api/account/bootstrap") return Promise.resolve(json(ownedBootstrapFixture({ profile: { email: "one@example.invalid" } })));
    if (path === "/api/backend/v1/features") return Promise.resolve(json({ features: {} }));
    if (path === "/api/backend/v1/credits/availability") return Promise.resolve(json({ catalog_enabled: false, execution_enabled: false, supported_operations: [] }));
    if (path === "/api/backend/resume/list") return Promise.resolve(json({ resumes: [{ id: 10, filename: "synthetic.pdf", source_available: true }] }));
    if (path === "/api/backend/v1/employer-jobs/applications") return Promise.resolve(json({ items: [] }));
    if (path.includes("/search-summaries?")) return Promise.resolve(json({ items: [], next_cursor: null }));
    if (path === "/api/backend/v1/employer-jobs/searches" && init.method === "POST") return Promise.resolve(json({
      id: "search_pending", kind: "search_preparation", status: "pending", as_of: "2026-10-29T00:00:00Z", desired_count: 3,
      accepted_quote: { unit_price: 2, pricing_version: "synthetic", requested_count: 3, maximum_credits: 6, search_input_fingerprint: "synthetic-input" },
      candidates_examined: 0, charged_credits: 0, reserved_credits: 6, refunded_credits: 0, completed_search_id: null, failure_reason: null,
    }));
    if (path === "/api/backend/v1/employer-jobs/application-batches/batch_one") return Promise.resolve(json(batch));
    if (path === "/api/backend/v1/employer-jobs/application-batches/batch_one/cancel" && init.method === "POST") {
      return Promise.resolve(json({ ...batch, status: "cancelled", cancelled_at: "2026-10-29T00:01:00Z" }));
    }
    throw new Error(`Unexpected synthetic request: ${path}`);
  });
  vi.stubGlobal("fetch", requests);
});
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function tree(page: React.ReactNode) {
  // Root layout always mounts Nav and these pages under this same owner client.
  return <QueryClientProvider client={client}><Nav />{page}</QueryClientProvider>;
}

it.each(["search", "batch"] as const)("refreshes the %s catalog once without abort/restart and preserves application refresh", async (page) => {
  const events = vi.spyOn(window, "dispatchEvent");
  if (page === "batch") {
    state.pathname = "/employer-jobs/batches/batch_one";
    client.setQueryData(applicationsKey, { items: [] });
  }
  render(tree(page === "search" ? <EmployerJobsPage /> : <EmployerBatchPage />));
  await waitFor(() => expect(client.getQueryData(["employer-jobs", "catalog"])).toEqual(catalog));
  if (page === "search") {
    await screen.findByRole("option", { name: /synthetic.pdf/ });
    fireEvent.change(screen.getByLabelText("Resume"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Target role"), { target: { value: "Python Engineer" } });
    fireEvent.change(screen.getByLabelText("Jobs to find"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs" }));
    await screen.findByRole("heading", { name: "Preparing your search" });
    await waitFor(() => expect(requests.mock.calls.filter(([path]) => path === "/api/backend/v1/employer-jobs/applications")).toHaveLength(2));
  } else {
    fireEvent.click(await screen.findByRole("button", { name: "Cancel remaining batch work" }));
    await screen.findByText(/Cancellation requested for remaining work/);
    expect(client.getQueryState(applicationsKey)?.isInvalidated).toBe(true);
  }
  await waitFor(() => expect(catalogReads).toHaveLength(1));
  expect(events.mock.calls.filter(([event]) => event.type === "refresh_analysis_units")).toHaveLength(1);
  expect(catalogReads[0].signal.aborted).toBe(false);
  expect(abortedCatalogReads).toBe(0);
  await act(async () => { catalogReads[0].resolve(json({ ...catalog, balance: 44 })); });
  await waitFor(() => expect(client.getQueryData<{ balance: number }>(["employer-jobs", "catalog"])?.balance).toBe(44));
  expect(requests.mock.calls.filter(([path]) => path === catalogPath)).toHaveLength(2); // initial + one refresh
  expect(requests.mock.calls.filter(([path]) => path === "/api/account/bootstrap")).toHaveLength(2);
  expect(requests.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  events.mockRestore();
});
