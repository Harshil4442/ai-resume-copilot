import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ id: "opportunity-one", tab: "resume", get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: state.id }), useSearchParams: () => new URLSearchParams(`tab=${state.tab}`) }));
vi.mock("../../../lib/api", () => ({ apiGet: state.get, apiPostJson: state.post, apiPatchJson: state.patch, apiDownload: vi.fn(),
  ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
vi.mock("../../../lib/analytics", () => ({ trackEvent: vi.fn() }));

import { ApiError } from "../../../lib/api";
import OpportunityPage from "./page";

let client: QueryClient;
let resumeId: number;
let quote: { analysis_pricing_version?: unknown; analysis_operation_units?: { resume_tailor?: unknown } };
const stamp = "2026-10-16T00:00:00Z";
function opportunity() {
  return { id: state.id, title: "Engineer", company: "Synthetic Company", location: "Remote", priority: "high",
    stage: "evaluating", resume_id: resumeId, latest_match_id: null, activity: [], contacts: [], reminders: [],
    resume_versions: [], notes: "", job_description: "Build reliable services.", created_at: stamp, updated_at: stamp };
}
function read(url: string) {
  if (url === "/public/billing/catalog") return Promise.resolve(quote);
  if (url === "/resume/list") return Promise.resolve({ resumes: [1, 2].map((id) => ({ id, filename: `synthetic-${id}.pdf`, source_available: true, source_format: "pdf" })) });
  if (url === `/v1/opportunities/${state.id}`) return Promise.resolve(opportunity());
  if (url.startsWith("/v1/evidence-items?")) return Promise.resolve([{ id: "evidence-one", title: "Approved fact", approval_state: "approved", evidence_text: "Built reliable services.", skills: [] }]);
  if (url.startsWith("/v1/analysis-runs/")) return Promise.resolve({ id: "run-one", opportunity_id: state.id, operation: "resume_tailor", status: "cancelled", estimated_units: 7, committed_units: 0 });
  throw new Error(`Unexpected local fixture GET ${url}`);
}
function tree() { return <QueryClientProvider client={client}><OpportunityPage /></QueryClientProvider>; }
async function page() {
  const result = render(tree());
  await screen.findByRole("heading", { name: "Engineer" });
  return result;
}
function generate(units = 7) { return screen.getByRole("button", { name: `Generate tailored version · ${units} AI analysis ${units === 1 ? "unit" : "units"}` }); }
function requests() { return state.post.mock.calls.filter(([url]) => url === "/v1/analysis-runs"); }

beforeEach(() => {
  state.id = "opportunity-one"; state.tab = "resume"; resumeId = 1;
  quote = { analysis_pricing_version: "price-reviewed", analysis_operation_units: { resume_tailor: 7 } };
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  state.get.mockReset().mockImplementation(read); state.post.mockReset(); state.patch.mockReset().mockImplementation(async (_url, body) => { resumeId = body.resume_id; return opportunity(); });
});
afterEach(() => { cleanup(); client.clear(); });

it("fetches the free catalog only when the resume tab opens and binds the displayed non-hardcoded price to the click", async () => {
  state.tab = "overview"; await page();
  expect(state.get.mock.calls.some(([url]) => url === "/public/billing/catalog")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Resume & evidence" }));
  await waitFor(() => expect(generate()).toBeEnabled());
  expect(screen.getByText(/These are separate from job service credits/)).toHaveTextContent("Generating accepts 7 AI analysis units.");
  expect(requests()).toHaveLength(0);
  state.post.mockResolvedValue({ id: "run-one", opportunity_id: state.id, operation: "resume_tailor", status: "cancelled", estimated_units: 7 });
  fireEvent.click(generate());
  await waitFor(() => expect(requests()).toHaveLength(1));
  expect(requests()[0]).toEqual(["/v1/analysis-runs", { operation: "resume_tailor", opportunity_id: "opportunity-one", input: {}, accepted_quote: { pricing_version: "price-reviewed", units: 7 } }, { "Idempotency-Key": expect.any(String) }]);
});

it("keeps the free source snapshot available while the price is loading or unavailable", async () => {
  let reject!: (error: Error) => void;
  state.get.mockImplementation((url: string) => url === "/public/billing/catalog" ? new Promise((_resolve, fail) => { reject = fail; }) : read(url));
  await page();
  expect(screen.getByRole("button", { name: "Generate tailored version" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save source snapshot" })).toBeEnabled();
  await act(async () => { reject(new Error("Unavailable")); });
  await screen.findByRole("button", { name: "Refresh tailoring price" });
  expect(screen.getByRole("button", { name: "Generate tailored version" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save source snapshot" })).toBeEnabled();
  expect(requests()).toHaveLength(0);
});

it.each([
  {}, { analysis_pricing_version: "", analysis_operation_units: { resume_tailor: 2 } },
  { analysis_pricing_version: "version", analysis_operation_units: { resume_tailor: "2" } },
  { analysis_pricing_version: "version", analysis_operation_units: { resume_tailor: -1 } },
  { analysis_pricing_version: "version", analysis_operation_units: { resume_tailor: 1.5 } },
])("does not infer a price from malformed or missing catalog fields %j", async (value) => {
  quote = value; await page();
  await screen.findByRole("button", { name: "Refresh tailoring price" });
  expect(screen.getByRole("button", { name: "Generate tailored version" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save source snapshot" })).toBeEnabled();
  expect(requests()).toHaveLength(0);
});

it("refreshes a refused quote without another POST, then requires a click accepting the new price and intent", async () => {
  state.post.mockRejectedValue(new ApiError("Review the current AI-unit quote before starting this analysis.", 409, { detail: { code: "analysis_quote_requires_review" } }));
  await page(); await waitFor(() => expect(generate()).toBeEnabled());
  quote = { analysis_pricing_version: "price-renewed", analysis_operation_units: { resume_tailor: 9 } };
  fireEvent.click(generate());
  await waitFor(() => expect(generate(9)).toBeEnabled());
  expect(requests()).toHaveLength(1);
  expect(state.get.mock.calls.filter(([url]) => url === "/public/billing/catalog")).toHaveLength(2);
  fireEvent.click(generate(9));
  await waitFor(() => expect(requests()).toHaveLength(2));
  expect(requests()[1][1].accepted_quote).toEqual({ pricing_version: "price-renewed", units: 9 });
  expect(requests()[1][2]["Idempotency-Key"]).not.toBe(requests()[0][2]["Idempotency-Key"]);
});

it("preserves the original idempotency key and accepted terms after a lost response despite a catalog change", async () => {
  state.post.mockRejectedValue(new TypeError("Network response lost"));
  await page(); await waitFor(() => expect(generate()).toBeEnabled()); fireEvent.click(generate());
  const retry = await screen.findByRole("button", { name: "Retry same request · 7 AI analysis units" });
  await act(async () => { client.setQueryData(["tailoring-price", state.id, 1], { pricing_version: "new-price", units: 12 }); });
  expect(retry).toBeEnabled(); expect(requests()).toHaveLength(1);
  fireEvent.click(retry); await waitFor(() => expect(requests()).toHaveLength(2));
  expect(requests()[1]).toEqual(requests()[0]);
});

it.each(["resume", "opportunity"])("starts a fresh reviewed intent when the %s changes", async (change) => {
  state.post.mockRejectedValue(new TypeError("Network response lost"));
  const rendered = await page(); await waitFor(() => expect(generate()).toBeEnabled()); fireEvent.click(generate());
  await screen.findByRole("button", { name: "Retry same request · 7 AI analysis units" });
  if (change === "resume") fireEvent.change(screen.getByRole("combobox", { name: "Resume" }), { target: { value: "2" } });
  else { state.id = "opportunity-two"; rendered.rerender(tree()); }
  await waitFor(() => expect(generate()).toBeEnabled()); fireEvent.click(generate());
  await waitFor(() => expect(requests()).toHaveLength(2));
  expect(requests()[1][2]["Idempotency-Key"]).not.toBe(requests()[0][2]["Idempotency-Key"]);
  expect(requests()[1][1].opportunity_id).toBe(state.id);
});

it.each([429, 503])("reads an existing run explicitly after HTTP%s without restarting generation", async (status) => {
  let statusReads = 0;
  state.get.mockImplementation((url: string) => url.startsWith("/v1/analysis-runs/")
    ? ++statusReads === 1 ? Promise.reject(new ApiError("Status unavailable", status, {}))
      : Promise.resolve({ id: "run-one", opportunity_id: state.id, operation: "resume_tailor", status: "cancelled", estimated_units: 7, committed_units: 0 })
    : read(url));
  state.post.mockResolvedValue({ id: "run-one", opportunity_id: state.id, operation: "resume_tailor", status: "queued", estimated_units: 7 });
  await page(); await waitFor(() => expect(generate()).toBeEnabled());
  fireEvent.click(generate());
  const statusCheck = await screen.findByRole("button", { name: "Check tailoring status" });
  expect(generate()).toBeDisabled(); expect(statusReads).toBe(1); expect(requests()).toHaveLength(1);
  fireEvent.click(statusCheck);
  await waitFor(() => expect(generate()).toBeEnabled());
  expect(statusReads).toBe(2); expect(requests()).toHaveLength(1);
});
