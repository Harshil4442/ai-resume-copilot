import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { apiGet, apiPostJson } from "../lib/api";
import type { AdmissionLimits, EmployerApplication, EmployerApplicationBatch, JobServiceCatalog } from "../lib/employerJobs";
import EmployerBatchPanel from "./EmployerBatchPanel";

vi.mock("../lib/api", () => ({ apiGet: vi.fn(), apiPostJson: vi.fn(), apiDownload: vi.fn() }));
vi.mock("./EmployerApplicationPanel", () => ({ ArtifactPreview: () => <p>Exact fixture file</p> }));
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; vi.resetAllMocks(); });

it.each([429, 503])("blocks queueing from cached approval after HTTP%s status refusal until a successful read", async (status) => {
  const limits: AdmissionLimits = { version: "fixture", daily_limit: 10, rolling_limit: 20, rolling_days: 7,
    pending_limit: 5, daily_credit_limit: 100, rolling_credit_limit: 200, batch_limit: 10,
    batch_credit_limit: 100, quote_hours: 24, day_boundary: "UTC", counting_policy: "fixture" };
  const expires = new Date(Date.now() + 86400000).toISOString();
  const quote = { candidate: limits, employer: { version: "fixture", daily_limit: 10, rolling_limit: 20,
    rolling_days: 7, evidence_url: null, evidence_note: "Fixture" }, employer_key: "employer_one", opening_key: "opening_one",
    quoted_at: new Date().toISOString(), expires_at: expires, policy_fingerprint: "p".repeat(64) };
  const application: EmployerApplication = { id: "app_one", status: "approved", application_mode: "api", resume_id: 1,
    resume_choice: "original", resume_version_id: null, batch_id: "batch_one", form: { version: "fixture", fields: [], consents: [] },
    answers: {}, consents: {}, missing_fields: [], package_digest: "a".repeat(64), credit_cost: 20, charged_credits: 0,
    requires_user_action: false, artifact: { sha256: "b".repeat(64), filename: "fixture.pdf", media_type: "application/pdf", size_bytes: 10, preview_url: "" },
    pricing_snapshot: { pricing_version: "fixture", unit_price: 20 }, admission_snapshot: quote,
    opening_key: quote.opening_key, employer_key: quote.employer_key,
    posting: { id: "job_one", external_id: "1", source_id: "source_one", title: "Fixture role", employer: "Fixture employer",
      location: "Remote", description: "Fixture", canonical_url: "https://employer.example/jobs/1", apply_url: "https://employer.example/apply/1",
      platform: "fixture", publication_at: null, last_checked_at: new Date().toISOString(), application_mode: "api" } };
  const batch: EmployerApplicationBatch = { id: "batch_one", status: "approved", package_digest: "c".repeat(64),
    items: [{ application_id: application.id, package_digest: application.package_digest, allowed_actions: ["fill", "upload", "submit"],
      credit_cost: 20, opening_key: quote.opening_key, employer_key: quote.employer_key,
      pricing_snapshot: application.pricing_snapshot!, admission_snapshot: quote }], admission_snapshot: limits,
    quoted_credits: 20, max_total_credits: 20, created_at: new Date().toISOString(), expires_at: expires,
    approved_at: new Date().toISOString(), cancelled_at: null, application_statuses: { app_one: "approved" } };
  const catalog: JobServiceCatalog = { enabled: true, auto_submit_enabled: true, balance: 40,
    search_credits_per_job: 1, apply_credits_per_job: 20, pricing_version: "fixture", max_search_jobs: 10,
    admission_limits: limits, sources: [{ id: "source_one", employer: "Fixture", platform: "fixture", careers_url: "https://employer.example",
      last_success_at: null, status: "active", application_mode: "api" }] };
  let batchReads = 0;
  vi.mocked(apiGet).mockImplementation(async (path) => {
    if (path.endsWith("/catalog")) return catalog;
    if (path.includes("/application-batches/")) {
      if (++batchReads === 2) throw Object.assign(new Error("Status unavailable"), { status });
      return batch;
    }
    return application;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(client);
  render(<QueryClientProvider client={client}><EmployerBatchPanel id="batch_one" onClose={vi.fn()} onChange={vi.fn()} /></QueryClientProvider>);
  const queue = await screen.findByRole("button", { name: "Queue these approved applications" });
  await waitFor(() => expect(queue).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Refresh batch status" }));
  await screen.findByText(/Could not load the exact batch/);
  expect(queue).toBeDisabled(); expect(batchReads).toBe(2); expect(apiPostJson).not.toHaveBeenCalled();
  fireEvent.click(queue); expect(apiPostJson).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh batch status" }));
  await waitFor(() => expect(queue).toBeEnabled());
  expect(batchReads).toBe(3); expect(apiPostJson).not.toHaveBeenCalled();
});
