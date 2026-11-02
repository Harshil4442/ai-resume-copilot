import { ownedBootstrapFixture } from "../../../lib/fixtures/ownedBootstrap";
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
  if (url === "/auth/bootstrap") return Promise.resolve(ownedBootstrapFixture());
  if (url === "/public/billing/catalog") return Promise.resolve(quote);
  if (url === "/resume/list") return Promise.resolve({ resumes: [1, 2].map((id) => ({ id, filename: `synthetic-${id}.pdf`, source_available: true, source_format: "pdf" })) });
  if (url === `/v1/opportunities/${state.id}`) return Promise.resolve(opportunity());
  if (url.startsWith("/v1/evidence-items?")) return Promise.resolve([{ id: "evidence-one", title: "Approved fact", approval_state: "approved", evidence_text: "Built reliable services.", skills: [], snapshot_fingerprint: "a".repeat(64) }]);
  if (url.startsWith("/v1/evidence-items/transcription-review/")) return Promise.resolve({ resume_id: resumeId, filename: `synthetic-${resumeId}.pdf`, source_format: "pdf", source_sha256: "c".repeat(64), assessment_fingerprint: "d".repeat(64), status: "not_required", sections: [], unmapped_text: null });
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

const originalTranscription = "Developed clear docu-\nmented records.";
function transcriptionSnapshot(fingerprint = "d") {
  return { resume_id: 1, filename: "actual-original.pdf", source_format: "pdf", source_sha256: "c".repeat(64),
    assessment_fingerprint: fingerprint.repeat(64), status: "needs_review", unmapped_text: null,
    sections: [{ section: "experience", original_text: originalTranscription, evidence_id: "evidence-one", evidence_fingerprint: "a".repeat(64), reviewed: false }] };
}
function ambiguousRead(url: string) {
  if (url.startsWith("/v1/evidence-items/transcription-review/")) return Promise.resolve(transcriptionSnapshot());
  if (url.startsWith("/v1/evidence-items?")) return Promise.resolve([{ id: "evidence-one", title: "Approved fact", approval_state: "approved", evidence_text: originalTranscription, skills: [], snapshot_fingerprint: "a".repeat(64) }]);
  return read(url);
}

it("shows full literal extraction and actual source, requiring explicit compare and visible-text confirmation", async () => {
  state.get.mockImplementation(ambiguousRead);
  state.post.mockResolvedValue({});
  await page();
  await screen.findByText(/Some line-end text is ambiguous/);
  fireEvent.click(screen.getByRole("button", { name: "Edit evidence" }));
  expect(screen.getByText((_text, node) => node?.tagName === "PRE" && node.textContent === originalTranscription)).toBeVisible();
  expect(screen.getByRole("link", { name: "View original PDF file" })).toHaveAttribute("href", "/resume/preview?resume=1");
  const confirm = screen.getByRole("button", { name: "Keep visible text and approve" });
  expect(confirm).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "I compared this complete section with my original file." }));
  expect(state.post).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(state.post).toHaveBeenCalledExactlyOnceWith("/v1/evidence-items/evidence-one/transcription-review", {
    expected_fingerprint: "a".repeat(64), expected_assessment_fingerprint: "d".repeat(64),
    section: "experience", evidence_text: originalTranscription, action: "confirm_visible_text",
  }));
});

it("freezes source and evidence versions for an explicit correction, preserving draft and locking after conflict", async () => {
  state.get.mockImplementation(ambiguousRead);
  state.post.mockRejectedValue(new ApiError("Changed", 409, { detail: { code: "evidence_refresh_required" } }));
  await page();
  await screen.findByText(/Some line-end text is ambiguous/);
  fireEvent.click(screen.getByRole("button", { name: "Edit evidence" }));
  const draft = "Developed clear documented records.";
  fireEvent.change(screen.getByRole("textbox", { name: "Edit Approved fact evidence" }), { target: { value: draft } });
  fireEvent.click(screen.getByRole("checkbox", { name: "I compared this complete section with my original file." }));
  await act(async () => { client.setQueryData(["transcription-review", 1], transcriptionSnapshot("e")); });
  fireEvent.click(screen.getByRole("button", { name: "Approve corrected transcription" }));
  await screen.findByRole("alert");
  expect(state.post).toHaveBeenCalledExactlyOnceWith("/v1/evidence-items/evidence-one/transcription-review", {
    expected_fingerprint: "a".repeat(64), expected_assessment_fingerprint: "d".repeat(64),
    section: "experience", evidence_text: draft, action: "correct_transcription",
  });
  expect(screen.getByRole("textbox", { name: "Edit Approved fact evidence" })).toHaveValue(draft);
  expect(screen.getByRole("button", { name: "Approve corrected transcription" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Keep visible text and approve" })).toBeDisabled();
  await act(async () => { await client.invalidateQueries({ queryKey: ["transcription-review", 1] }); });
  expect(state.post).toHaveBeenCalledTimes(1);
});

it("does not offer transcription approval when the source review is unavailable or unmapped", async () => {
  state.get.mockImplementation((url: string) => url.startsWith("/v1/evidence-items/transcription-review/")
    ? Promise.resolve({ ...transcriptionSnapshot(), status: "editable_source_required", unmapped_text: originalTranscription }) : ambiguousRead(url));
  await page();
  await screen.findByText(/This extraction cannot map every ambiguous section exactly/);
  fireEvent.click(screen.getByRole("button", { name: "Edit evidence" }));
  expect(screen.queryByRole("button", { name: "Keep visible text and approve" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Upload an editable source" })).toHaveAttribute("href", expect.stringContaining("/resume?opportunity="));
  expect(state.post).not.toHaveBeenCalled();
});

it("lets a candidate explicitly restore complete literal text from an older edited fact without auto-approval", async () => {
  state.get.mockImplementation((url: string) => url.startsWith("/v1/evidence-items?") ? read(url) : ambiguousRead(url));
  await page();
  await screen.findByText(/Some line-end text is ambiguous/);
  fireEvent.click(screen.getByRole("button", { name: "Edit evidence" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "I compared this complete section with my original file." }));
  expect(screen.getByRole("button", { name: "Keep visible text and approve" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Use original extracted text" }));
  expect(screen.getByRole("textbox", { name: "Edit Approved fact evidence" })).toHaveValue(originalTranscription);
  expect(screen.getByRole("checkbox", { name: "I compared this complete section with my original file." })).not.toBeChecked();
  expect(state.post).not.toHaveBeenCalled();
});

it.each([["Approve evidence", "approved"], ["Reject evidence", "rejected"]])("binds %s to the exact rendered evidence version", async (name, reviewState) => {
  state.patch.mockResolvedValue({});
  await page();
  fireEvent.click(await screen.findByRole("button", { name }));
  await waitFor(() => expect(state.patch).toHaveBeenCalledTimes(1));
  expect(state.patch).toHaveBeenCalledWith("/v1/evidence-items/evidence-one", {
    approval_state: reviewState, expected_fingerprint: "a".repeat(64),
  });
});

it("keeps mutations locked when conflict refresh fails, until an explicit successful read", async () => {
  let unavailable = false;
  state.get.mockImplementation((url: string) => url.startsWith("/v1/evidence-items?") && unavailable
    ? Promise.reject(new Error("Read unavailable")) : read(url));
  state.patch.mockRejectedValue(new ApiError("Changed", 409, { detail: { code: "evidence_refresh_required" } }));
  await page();
  unavailable = true;
  fireEvent.click(await screen.findByRole("button", { name: "Approve evidence" }));
  const refresh = await screen.findByRole("button", { name: "Refresh evidence for review" });
  for (const name of ["Edit evidence", "Approve evidence", "Reject evidence"]) expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(state.patch).toHaveBeenCalledTimes(1);
  unavailable = false;
  fireEvent.click(refresh);
  await waitFor(() => expect(screen.getByRole("button", { name: "Approve evidence" })).toBeEnabled());
  expect(state.patch).toHaveBeenCalledTimes(1);
});

it("freezes the opened editor version across refetch and preserves a stale draft without retry", async () => {
  state.patch.mockRejectedValue(new ApiError("Changed", 409, { detail: { code: "evidence_refresh_required" } }));
  await page();
  fireEvent.click(await screen.findByRole("button", { name: "Edit evidence" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Edit Approved fact evidence" }), { target: { value: "My unsaved draft." } });
  await act(async () => { client.setQueryData(["evidence", 1], [{ id: "evidence-one", title: "Approved fact", approval_state: "pending", evidence_text: "Newer saved evidence.", skills: [], snapshot_fingerprint: "b".repeat(64) }]); });
  expect(screen.getByRole("button", { name: "Approve evidence" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save edit" }));
  await screen.findByRole("alert");
  expect(state.patch).toHaveBeenCalledExactlyOnceWith("/v1/evidence-items/evidence-one", {
    evidence_text: "My unsaved draft.", expected_fingerprint: "a".repeat(64),
  });
  expect(screen.getByRole("textbox", { name: "Edit Approved fact evidence" })).toHaveValue("My unsaved draft.");
  expect(screen.getByRole("button", { name: "Save edit" })).toBeDisabled();
});

it("refreshes a stale approval without replay and requires a new click on the new version", async () => {
  let current = "a";
  state.get.mockImplementation((url: string) => url.startsWith("/v1/evidence-items?")
    ? Promise.resolve([{ id: "evidence-one", title: "Approved fact", approval_state: "pending", evidence_text: `${current} saved fact.`, skills: [], snapshot_fingerprint: current.repeat(64) }]) : read(url));
  state.patch.mockRejectedValueOnce(new ApiError("Changed", 409, { detail: { code: "evidence_refresh_required" } })).mockResolvedValue({});
  await page();
  current = "b";
  fireEvent.click(await screen.findByRole("button", { name: "Approve evidence" }));
  await screen.findByText("b saved fact.");
  expect(state.patch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Approve evidence" }));
  await waitFor(() => expect(state.patch).toHaveBeenCalledTimes(2));
  expect(state.patch.mock.calls[1][1]).toEqual({ approval_state: "approved", expected_fingerprint: "b".repeat(64) });
});

it.each([undefined, "", "invalid", "A".repeat(64), 123])("locks evidence mutations without a valid server version %s", async (fingerprint) => {
  state.get.mockImplementation((url: string) => url.startsWith("/v1/evidence-items?")
    ? Promise.resolve([{ id: "evidence-one", title: "Approved fact", approval_state: "pending", evidence_text: "Unversioned fact.", skills: [], snapshot_fingerprint: fingerprint }]) : read(url));
  await page();
  for (const name of ["Edit evidence", "Approve evidence", "Reject evidence"]) expect(await screen.findByRole("button", { name })).toBeDisabled();
  expect(state.patch).not.toHaveBeenCalled();
});

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

const serviceQuote = { schema_version: "unified-service-quote-v1", quote_id: "csq_reviewed", operation: "resume_tailor", input_fingerprint: "b".repeat(64),
  route_id: "qualified-tailor", route_version: "1", mode: "enhanced", tariff_version: "credit-services-v1", credits: 8,
  deliverable: "A tailored resume that preserves its original layout.", supported_limits: { max_pages: 2, max_edits: 5 },
  created_at: "2026-10-27T10:00:00Z", expires_at: "2026-10-27T10:15:00Z", server_time: "2026-10-27T10:00:00Z" };
function enableCreditExecution(operations = ["resume_tailor", "resume_tailor_extended", "interview_questions", "enhanced_job_match"]) {
  state.get.mockImplementation((url: string) => url === "/auth/bootstrap"
    ? Promise.resolve(ownedBootstrapFixture({ availability: { catalog_enabled: true, execution_enabled: operations.length > 0, supported_operations: operations } })) : read(url));
  state.post.mockImplementation(async (url, body) => {
    if (url === "/v1/credits/analysis-reuse") return { reused_run: null };
    if (url === "/v1/credits/service-quotes") return { ...serviceQuote, operation: body.operation === "job_match" ? "enhanced_job_match" : body.operation === "resume_tailor" && body.input.tailoring_scope === "extended" ? "resume_tailor_extended" : body.operation,
      credits: body.input.tailoring_scope === "extended" ? 15 : body.operation === "interview_questions" ? 5 : 8 };
    return { id: "run-one", operation: body.operation, status: "queued", estimated_units: 8, billing_currency: "credits" };
  });
}
async function reviewTailoring() {
  await page(); const button = screen.getByRole("button", { name: "Generate tailored version" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  return screen.findByRole("button", { name: "Approve 8 credits" });
}
it("reviews the source and exact unified quote before tailoring", async () => {
  enableCreditExecution(); const approve = await reviewTailoring();
  expect(screen.getByText("synthetic-1.pdf", { selector: "dd" })).toBeVisible(); expect(requests()).toHaveLength(0);
  expect(state.get.mock.calls.some(([url]) => url === "/public/billing/catalog")).toBe(false);
  fireEvent.click(approve); await waitFor(() => expect(requests()).toHaveLength(1));
  expect(requests()[0][1]).toEqual({ operation: "resume_tailor", opportunity_id: state.id, input: { tailoring_scope: "standard" }, accepted_service_quote_id: "csq_reviewed" });
});
it("blocks a reviewed source after changing resumes and permits cancellation without a charge", async () => {
  enableCreditExecution(); await reviewTailoring();
  fireEvent.change(screen.getByRole("combobox", { name: "Resume" }), { target: { value: "2" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Approve 8 credits" })).toBeDisabled());
  expect(requests()).toHaveLength(0); fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("button", { name: "Approve 8 credits" })).toBeNull();
});
it("makes extended tailoring an explicit separately quoted scope", async () => {
  enableCreditExecution(); await page();
  fireEvent.change(await screen.findByRole("combobox", { name: "Tailoring scope" }), { target: { value: "extended" } });
  const button = screen.getByRole("button", { name: "Generate tailored version" }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  fireEvent.click(await screen.findByRole("button", { name: "Approve 15 credits" }));
  await waitFor(() => expect(requests()).toHaveLength(1)); expect(requests()[0][1].input).toEqual({ tailoring_scope: "extended" });
});
it("retries a lost approved response with its original quote, payload and idempotency key", async () => {
  enableCreditExecution(); const original = state.post.getMockImplementation()!;
  state.post.mockImplementation((url, ...rest) => url === "/v1/analysis-runs" ? Promise.reject(new TypeError("Response lost")) : original(url, ...rest));
  fireEvent.click(await reviewTailoring());
  fireEvent.click(await screen.findByRole("button", { name: "Retry same approved request" }));
  await waitFor(() => expect(requests()).toHaveLength(2)); expect(requests()[1]).toEqual(requests()[0]);
  expect(state.post.mock.calls.filter(([url]) => url === "/v1/credits/service-quotes")).toHaveLength(1);
});
it.each(["queued", "running", "succeeded"])("reuses a verified %s run without requesting a quote or another run", async (status) => {
  enableCreditExecution(); state.post.mockResolvedValue({ reused_run: { id: "run-one", operation: "resume_tailor", status } });
  await page(); const button = screen.getByRole("button", { name: "Generate tailored version" }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await waitFor(() => expect(state.get.mock.calls.some(([url]) => url === "/v1/analysis-runs/run-one")).toBe(true));
  expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/credits/analysis-reuse"]);
});
it("requests exactly eight enhanced questions after credit approval", async () => {
  state.tab = "interview"; enableCreditExecution(); await page();
  fireEvent.change(screen.getByRole("combobox", { name: "Question mode" }), { target: { value: "enhanced" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate questions" }));
  const approve = await screen.findByRole("button", { name: "Approve 5 credits" }); expect(requests()).toHaveLength(0); fireEvent.click(approve);
  await waitFor(() => expect(requests()).toHaveLength(1)); expect(requests()[0][1].input).toEqual({ num_questions: 8, mode: "enhanced" });
});
it("keeps snapshots accessible when no paid route is qualified", async () => {
  enableCreditExecution([]); await page(); expect(screen.getByRole("button", { name: "Generate tailored version" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save source snapshot" })).toBeEnabled(); expect(state.post).not.toHaveBeenCalled();
});


it("allows a fresh review only after an explicit no-run quote rejection", async () => {
  enableCreditExecution(); const original = state.post.getMockImplementation()!;
  state.post.mockImplementation((url, ...rest) => url === "/v1/analysis-runs"
    ? Promise.reject(new ApiError("Quote expired", 409, { detail: { code: "credit_service_quote_rejected", run_created: false } })) : original(url, ...rest));
  fireEvent.click(await reviewTailoring());
  await screen.findByText("Quote expired");
  expect(screen.getByRole("button", { name: "Approve 8 credits" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh service quote" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Approve 8 credits" })).toBeEnabled());
  expect(requests()).toHaveLength(1);
  expect(state.post.mock.calls.filter(([url]) => url === "/v1/credits/service-quotes")).toHaveLength(2);
});


it("recovers a quote already attached to a run by reading that run", async () => {
  enableCreditExecution(); const original = state.post.getMockImplementation()!;
  state.post.mockImplementation((url, ...rest) => url === "/v1/analysis-runs"
    ? Promise.reject(new ApiError("Already accepted", 409, { detail: { code: "credit_service_quote_in_use", run_created: true, run_id: "run-one" } })) : original(url, ...rest));
  fireEvent.click(await reviewTailoring());
  await waitFor(() => expect(state.get.mock.calls.some(([url]) => url === "/v1/analysis-runs/run-one")).toBe(true));
  expect(requests()).toHaveLength(1);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Approve 8 credits" })).toBeNull());
});


it("labels basic comparison free only when the server supplies its funded allowance", async () => {
  state.tab = "overview";
  state.get.mockImplementation((url: string) => url === "/auth/bootstrap" ? Promise.resolve(ownedBootstrapFixture({ availability: { catalog_enabled: false,
    execution_enabled: false, supported_operations: [], free_core_operations: ["basic_match"],
    free_core_limits: { basic_match: { per_account_30_days: 10, max_input_bytes: 10000, max_source_records: 100 } } } })) : read(url));
  await page(); expect(screen.getByRole("combobox", { name: "Match mode · 0 credits" })).toBeVisible();
  expect(screen.getByText(/Basic comparison allowance: up to 10 new requests in a rolling 30-day period/)).toBeVisible();
  expect(state.post).not.toHaveBeenCalled();
});


it("routes the empty-state enhanced match action through the same credit review", async () => {
  state.tab = "overview"; enableCreditExecution(); await page();
  fireEvent.change(screen.getByRole("combobox", { name: "Match mode" }), { target: { value: "enhanced" } });
  const buttons = screen.getAllByRole("button", { name: "Run match" }); fireEvent.click(buttons[buttons.length - 1]);
  expect(await screen.findByRole("button", { name: "Approve 8 credits" })).toBeVisible();
  expect(requests()).toHaveLength(0);
});

it("refreshes one shared account snapshot once for a completed tailoring run without cancelling the pending read", async () => {
  const pending: ((value: unknown) => void)[] = [];
  const signals: AbortSignal[] = [];
  state.get.mockImplementation((url: string, signal?: AbortSignal) => {
    if (url === "/auth/bootstrap" && state.get.mock.calls.filter(([path]) => path === url).length > 1) {
      signals.push(signal!);
      return new Promise((resolve) => { pending.push(resolve); });
    }
    if (url.endsWith("/result")) return Promise.resolve({ result: {} });
    if (url.startsWith("/v1/analysis-runs/")) return Promise.resolve({ id: "run-one", opportunity_id: state.id,
      operation: "resume_tailor", status: "succeeded", estimated_units: 7, committed_units: 7 });
    return read(url);
  });
  state.post.mockResolvedValue({ id: "run-one", operation: "resume_tailor", status: "queued", estimated_units: 7 });
  await page(); await waitFor(() => expect(generate()).toBeEnabled()); fireEvent.click(generate());
  await waitFor(() => expect(state.get.mock.calls.filter(([path]) => path === "/auth/bootstrap")).toHaveLength(2));
  expect(signals).toHaveLength(1); expect(signals[0].aborted).toBe(false);
  await act(async () => { pending.forEach((finish) => finish(ownedBootstrapFixture())); });
  expect(state.get.mock.calls.filter(([path]) => path === "/auth/bootstrap")).toHaveLength(2);
  expect(requests()).toHaveLength(1);
});
