import { ownedBootstrapFixture } from "../../../lib/fixtures/ownedBootstrap";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ id: "opportunity-one", tab: "resume", get: vi.fn(), wire: vi.fn(), direct: vi.fn(), post: vi.fn(), patch: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: state.id }), useSearchParams: () => new URLSearchParams(`tab=${state.tab}`) }));
vi.mock("../../../lib/api", () => ({ apiGet: (url: string, signal?: AbortSignal) => fixtureTransport(url, signal), apiPostJson: state.post, apiPatchJson: state.patch, apiDownload: vi.fn(),
  ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: "authenticated", data: { user: { id: "fixture-owner" } } }) }));
vi.mock("../../../lib/analytics", () => ({ trackEvent: vi.fn() }));

import { ApiError } from "../../../lib/api";
import { trackEvent } from "../../../lib/analytics";
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
  if (url === "/resume/page?limit=20") return Promise.resolve({ resumes: [1, 2].map((id) => ({ id, filename: `synthetic-${id}.pdf`, source_available: true, source_format: "pdf" })) });
  if (url === `/v1/opportunities/${state.id}`) return Promise.resolve(opportunity());
  if (url.startsWith("/v1/evidence-items?")) return Promise.resolve([{ id: "evidence-one", title: "Approved fact", approval_state: "approved", evidence_text: "Built reliable services.", skills: [], snapshot_fingerprint: "a".repeat(64) }]);
  if (url.startsWith("/v1/evidence-items/transcription-review/")) return Promise.resolve({ resume_id: resumeId, filename: `synthetic-${resumeId}.pdf`, source_format: "pdf", source_sha256: "c".repeat(64), assessment_fingerprint: "d".repeat(64), status: "not_required", sections: [], unmapped_text: null });
  if (url.startsWith("/v1/analysis-runs/")) return Promise.resolve({ id: "run-one", opportunity_id: state.id, operation: "resume_tailor", status: "cancelled", estimated_units: 7, committed_units: 0 });
  throw new Error(`Unexpected local fixture GET ${url}`);
}
// Preserve the existing behavioural fixtures while adapting their data to the
// compact wire contract. `wire` records actual client requests; legacy reads
// below only build local fixture values and never stand for extra HTTP calls.
async function fixtureTransport(url: string, signal?: AbortSignal) {
  state.wire(url, signal);
  const direct = state.direct(url, signal);
  if (direct !== undefined) return direct;
  const prefix = `/v1/opportunities/${state.id}/workspace`;
  if (!url.startsWith(prefix)) return state.get(url, signal);
  const legacy = await state.get(`/v1/opportunities/${state.id}`, signal);
  if (url.startsWith(`${prefix}?`)) {
    const tab = new URLSearchParams(url.split("?")[1]).get("tab");
    const resumePage = await state.get("/resume/page?limit=20", signal);
    const facts = tab === "resume" ? await state.get(`/v1/evidence-items?resume_id=${legacy.resume_id}`, signal) : [];
    const metadata = (entry: Record<string, unknown>) => ({ id: entry.id, title: entry.title, category: "experience", resume_id: legacy.resume_id, approval_state: entry.approval_state });
    return { opportunity: legacy, resumes: { ...resumePage, selected_resume: resumePage.resumes.find((row: { id: number }) => row.id === legacy.resume_id) || null },
      pages: { resume_versions: { items: legacy.resume_versions, next_cursor: null }, contacts: tab === "overview" ? { items: legacy.contacts, next_cursor: null } : null,
        reminders: tab === "overview" ? { items: legacy.reminders, next_cursor: null } : null, evidence: tab === "resume" ? { items: facts.map(metadata), next_cursor: null } : null,
        activity: tab === "activity" ? { items: legacy.activity, next_cursor: null } : null }, match: null, match_source_state: "none", submitted_resume_version_id: null,
      submitted_resume_version: null, selected_resume_version: null, approved_evidence_count: tab === "resume" ? facts.filter((row: { approval_state: string }) => row.approval_state === "approved").length : null };
  }
  if (url.includes("/evidence/")) {
    const facts = await state.get(`/v1/evidence-items?resume_id=${legacy.resume_id}`, signal);
    const fact = facts.find((row: { id: string }) => row.id === url.split("/").at(-1));
    let transcription;
    try { transcription = await state.get(`/v1/evidence-items/transcription-review/${legacy.resume_id}`, signal); }
    catch { return { evidence: { ...fact, resume_id: legacy.resume_id }, transcription: null, transcription_unavailable: true }; }
    return { evidence: { ...fact, resume_id: legacy.resume_id }, transcription: { ...transcription, section: transcription.sections.find((section: { evidence_id: string }) => section.evidence_id === fact.id) || null }, transcription_unavailable: false };
  }
  if (url.includes("/resume_versions/")) return legacy.resume_versions.find((row: { id: string }) => row.id === url.split("/").at(-1));
  if (url.endsWith("/body")) return legacy;
  throw new Error(`Unexpected compact fixture GET ${url}`);
}
function detailKey() { return ["workspace", "authenticated:fixture-owner", state.id, "detail", resumeId, "evidence", "evidence-one"]; }
function tree() { return <QueryClientProvider client={client}><OpportunityPage /></QueryClientProvider>; }
async function page() {
  const result = render(tree());
  await screen.findByRole("heading", { name: "Engineer" });
  if (state.tab === "resume") {
    const open = await screen.findByRole("button", { name: "Open Approved fact for review" });
    fireEvent.click(open);
    await screen.findByRole("button", { name: "Edit evidence" });
  }
  return result;
}
function generate(units = 7) { return screen.getByRole("button", { name: `Generate tailored version · ${units} AI analysis ${units === 1 ? "unit" : "units"}` }); }
function requests() { return state.post.mock.calls.filter(([url]) => url === "/v1/analysis-runs"); }

beforeEach(() => {
  vi.mocked(trackEvent).mockClear();
  state.id = "opportunity-one"; state.tab = "resume"; resumeId = 1;
  quote = { analysis_pricing_version: "price-reviewed", analysis_operation_units: { resume_tailor: 7 } };
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  state.wire.mockReset(); state.direct.mockReset(); state.get.mockReset().mockImplementation(read); state.post.mockReset(); state.patch.mockReset().mockImplementation(async (_url, body) => { resumeId = body.resume_id; return opportunity(); });
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
  await act(async () => { client.setQueryData(detailKey(), (old: { evidence: unknown }) => ({ ...old, transcription: { ...transcriptionSnapshot("e"), section: transcriptionSnapshot("e").sections[0] } })); });
  fireEvent.click(screen.getByRole("button", { name: "Approve corrected transcription" }));
  await screen.findByRole("alert");
  expect(state.post).toHaveBeenCalledExactlyOnceWith("/v1/evidence-items/evidence-one/transcription-review", {
    expected_fingerprint: "a".repeat(64), expected_assessment_fingerprint: "d".repeat(64),
    section: "experience", evidence_text: draft, action: "correct_transcription",
  });
  expect(screen.getByRole("textbox", { name: "Edit Approved fact evidence" })).toHaveValue(draft);
  expect(screen.getByRole("button", { name: "Approve corrected transcription" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Keep visible text and approve" })).toBeDisabled();
  await act(async () => { await client.invalidateQueries({ queryKey: detailKey() }); });
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
    ? Promise.reject(new ApiError("Read unavailable", 503, {})) : read(url));
  state.patch.mockRejectedValue(new ApiError("Changed", 409, { detail: { code: "evidence_refresh_required" } }));
  await page();
  unavailable = true;
  fireEvent.click(await screen.findByRole("button", { name: "Approve evidence" }));
  const refresh = await screen.findByRole("button", { name: "Refresh workspace" });
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
  await act(async () => { client.setQueryData(detailKey(), (old: { transcription: unknown }) => ({ ...old, evidence: { id: "evidence-one", title: "Approved fact", approval_state: "pending", evidence_text: "Newer saved evidence.", skills: [], snapshot_fingerprint: "b".repeat(64) } })); });
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
  const original = state.get.getMockImplementation()!;
  state.get.mockImplementation((url: string, signal?: AbortSignal) => {
    if (url === "/v1/analysis-runs/run-one") return Promise.resolve({ id: "run-one", opportunity_id: state.id,
      operation: "resume_tailor", status: "succeeded", estimated_units: 8, committed_units: 8, billing_currency: "credits" });
    if (url === "/v1/analysis-runs/run-one/result") return Promise.resolve({ result: {} });
    return original(url, signal);
  });
  await page(); const button = screen.getByRole("button", { name: "Generate tailored version" }); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await waitFor(() => expect(state.wire.mock.calls.some(([url]) => url === "/v1/analysis-runs/run-one/result")).toBe(true));
  expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/credits/analysis-reuse"]);
  expect(state.wire.mock.calls.filter(([url]) => url === "/auth/bootstrap")).toHaveLength(status === "succeeded" ? 1 : 2);
  expect(state.wire.mock.calls.filter(([url]) => url.includes("/workspace?"))).toHaveLength(status === "succeeded" ? 1 : 2);
  expect(vi.mocked(trackEvent).mock.calls.filter(([name]) => name === "analysis_completed")).toHaveLength(status === "succeeded" ? 0 : 1);
});
it("requests exactly eight enhanced questions after credit approval", async () => {
  state.tab = "interview"; enableCreditExecution(); await page();
  fireEvent.change(screen.getByRole("combobox", { name: "Question mode" }), { target: { value: "enhanced" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate questions" }));
  const approve = await screen.findByRole("button", { name: "Approve 5 credits" }); expect(requests()).toHaveLength(0); fireEvent.click(approve);
  await waitFor(() => expect(requests()).toHaveLength(1)); expect(requests()[0][1].input).toEqual({ num_questions: 8, mode: "enhanced" });
});
it("reopens saved enhanced interview questions when catalog and execution are paused", async () => {
  state.tab = "interview";
  const run = { id: "saved-interview", opportunity_id: state.id, operation: "interview_questions", status: "succeeded",
    estimated_units: 5, committed_units: 5, billing_currency: "credits", usage_state: "committed" };
  const question = "How did you improve reliability for the service?";
  state.get.mockImplementation((url: string) => {
    if (url === "/auth/bootstrap") return Promise.resolve(ownedBootstrapFixture({ availability: { catalog_enabled: false, execution_enabled: false, supported_operations: [] } }));
    if (url === `/v1/analysis-runs/${run.id}`) return Promise.resolve(run);
    if (url === `/v1/analysis-runs/${run.id}/result`) return Promise.resolve({ id: run.id, operation: run.operation,
      result: { mode: "enhanced", questions: [{ question, answer_guidance: "Explain your contribution and its outcome." }] } });
    return read(url);
  });
  state.post.mockResolvedValue({ reused_run: run });
  await page();
  expect(screen.getByRole("combobox", { name: /Question mode/ })).toHaveValue("curated");
  expect(screen.queryByRole("heading", { name: "Saved interviews" })).not.toBeInTheDocument();
  expect(state.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Find saved result for Interview preparation · no charge" }));
  expect(await screen.findByRole("heading", { name: question })).toBeVisible();
  expect(screen.getByText("Your existing request was loaded. No new credits were charged.")).toBeVisible();
  expect(screen.queryByRole("button", { name: /Approve .* credits/ })).not.toBeInTheDocument();
  expect(state.post.mock.calls).toEqual([["/v1/credits/analysis-reuse", { operation: "interview_questions",
    opportunity_id: state.id, input: { num_questions: 8, mode: "enhanced" } }]]);
});
it("opens an older saved interview directly without billing or progress polling", async () => {
  state.tab = "interview";
  const role = state.id;
  const run = { id: "retained-interview", completed_at: stamp };
  const expired = { id: "expired-interview", completed_at: stamp };
  const question = "How did you improve reliability in your previous role?";
  const resultsPage = `/v1/opportunities/${role}/workspace/interview_results`;
  const answerPath = `/v1/analysis-runs/${run.id}/result`;
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) return Promise.resolve({ ...matchWorkspace("none"), pages: {
      ...matchWorkspace("none").pages, interview_results: { items: [run], next_cursor: "saved-cursor" },
    } });
    if (url.startsWith(resultsPage)) return Promise.resolve({ items: [expired], next_cursor: null });
    if (url === answerPath) return Promise.resolve({ ...run, operation: "interview_questions",
      result: { opportunity_id: role, mode: "enhanced", questions: [{ question, answer: "Describe your verified contribution." }] } });
    if (url === `/v1/analysis-runs/${expired.id}/result`) return Promise.reject(new ApiError("Deleted under retention policy.", 410, null));
  });
  state.get.mockImplementation((url: string) => url === "/auth/bootstrap"
    ? Promise.resolve(ownedBootstrapFixture({ availability: { catalog_enabled: false, execution_enabled: false, supported_operations: [] } }))
    : read(url));
  await page();
  const savedResult = screen.getByRole("button", { name: /Open saved interview from/ });
  const runReads = () => state.wire.mock.calls.map(([url]) => url).filter(url => url.startsWith("/v1/analysis-runs/"));
  expect(runReads()).toEqual([]);
  expect(state.wire.mock.calls.filter(([url]) => url.includes("/workspace?"))).toHaveLength(1);
  fireEvent.click(savedResult);
  expect(await screen.findByRole("heading", { name: question })).toBeVisible();
  expect(runReads()).toEqual([answerPath]);
  expect(state.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Older interviews" }));
  const older = await screen.findByRole("button", { name: /Open saved interview from/ });
  expect(screen.queryByRole("heading", { name: question })).not.toBeInTheDocument();
  fireEvent.click(older);
  expect(await screen.findByText("This saved interview is no longer available.")).toBeVisible();
  expect(runReads()).toEqual([answerPath, `/v1/analysis-runs/${expired.id}/result`]);
  expect(state.post).not.toHaveBeenCalled();
});
it("explains an empty legacy saved interview without starting a new request", async () => {
  state.tab = "interview";
  const run = { id: "empty-legacy-interview", completed_at: stamp };
  const resultPath = `/v1/analysis-runs/${run.id}/result`;
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) return Promise.resolve({ ...matchWorkspace("none"), pages: {
      ...matchWorkspace("none").pages, interview_results: { items: [run], next_cursor: null },
    } });
    if (url === resultPath) return Promise.resolve({ ...run, operation: "interview_questions",
      result: { opportunity_id: state.id, questions: [] } });
  });
  await page();
  fireEvent.click(screen.getByRole("button", { name: /Open saved interview from/ }));
  expect(await screen.findByText("No questions were saved for this interview.")).toBeVisible();
  expect(state.wire.mock.calls.map(([url]) => url).filter(url => url.startsWith("/v1/analysis-runs/"))).toEqual([resultPath]);
  expect(state.post).not.toHaveBeenCalled();
});
it("keeps a completed interview lookup from reloading history or recording another completion", async () => {
  state.tab = "interview";
  const run = { id: "existing-interview", opportunity_id: state.id, operation: "interview_questions", status: "succeeded",
    estimated_units: 5, committed_units: 5, billing_currency: "credits", usage_state: "committed" };
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) return Promise.resolve({ ...matchWorkspace("none"), pages: {
      ...matchWorkspace("none").pages, interview_results: { items: [{ id: run.id, completed_at: stamp }], next_cursor: null },
    } });
    if (url === `/v1/analysis-runs/${run.id}`) return Promise.resolve(run);
    if (url === `/v1/analysis-runs/${run.id}/result`) return Promise.resolve({ ...run,
      result: { opportunity_id: state.id, mode: "enhanced", questions: [{ question: "A previously saved question?" }] } });
  });
  state.post.mockResolvedValue({ reused_run: run });
  await page();
  fireEvent.click(screen.getByRole("button", { name: "Find saved result for Interview preparation · no charge" }));
  expect(await screen.findByRole("heading", { name: "A previously saved question?" })).toBeVisible();
  expect(state.wire.mock.calls.filter(([url]) => url.includes("/workspace?"))).toHaveLength(1);
  expect(state.wire.mock.calls.filter(([url]) => url === "/auth/bootstrap")).toHaveLength(1);
  expect(trackEvent).not.toHaveBeenCalledWith("analysis_completed", expect.anything());
});
it("records completion when a saved interview lookup races an already-observed run", async () => {
  state.tab = "interview";
  const run = { id: "queued-interview", opportunity_id: state.id, operation: "interview_questions",
    status: "succeeded", estimated_units: 5, committed_units: 5, billing_currency: "credits", usage_state: "committed" };
  let finishStatus: (value: typeof run) => void = () => {};
  state.direct.mockImplementation((url: string) => {
    if (url === `/v1/analysis-runs/${run.id}`) return new Promise<typeof run>((finish) => { finishStatus = finish; });
    if (url === `/v1/analysis-runs/${run.id}/result`) return Promise.resolve({ ...run,
      result: { opportunity_id: state.id, mode: "enhanced", questions: [{ question: "The tracked interview question?" }] } });
  });
  state.post.mockResolvedValueOnce({ reused_run: { ...run, status: "queued", committed_units: 0 } })
    .mockResolvedValueOnce({ reused_run: run });
  await page();
  const findSaved = screen.getByRole("button", { name: "Find saved result for Interview preparation · no charge" });
  fireEvent.click(findSaved);
  await waitFor(() => expect(state.wire.mock.calls.filter(([url]) => url === `/v1/analysis-runs/${run.id}`)).toHaveLength(1));
  await waitFor(() => expect(findSaved).toBeEnabled());
  await act(async () => fireEvent.click(findSaved));
  await waitFor(() => expect(findSaved).toBeEnabled());
  expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/credits/analysis-reuse", "/v1/credits/analysis-reuse"]);
  await act(async () => finishStatus(run));
  expect(await screen.findByRole("heading", { name: "The tracked interview question?" })).toBeVisible();
  expect(state.wire.mock.calls.filter(([url]) => url === "/auth/bootstrap")).toHaveLength(2);
  expect(vi.mocked(trackEvent).mock.calls.filter(([name]) => name === "analysis_completed")).toHaveLength(1);
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


function historicalVersionRead(url: string) {
  if (url === `/v1/opportunities/${state.id}`) return Promise.resolve({ ...opportunity(), resume_versions: [{
    id: "off-page-version", resume_id: 3, version_number: 1, label: "Off-page history", approval_state: "draft",
    evidence_ids: [], structured_content: { format_preservation: "source", source_format: "pdf", source_edits: [] },
    created_at: stamp, updated_at: stamp, submitted_at: null,
  }] });
  return read(url);
}

it("distinguishes unrequested, loading and failed history metadata, and enables source controls only after a confirmed retained row", async () => {
  let reject!: (error: Error) => void;
  state.get.mockImplementation((url: string) => url === "/resume/page?limit=1&current_id=3"
    ? new Promise((_resolve, fail) => { reject = fail; }) : historicalVersionRead(url));
  await page();
  fireEvent.click(await screen.findByText("Open version 1 for review"));
  fireEvent.click(await screen.findByText("Review source snapshot"));
  expect(screen.getByText(/The original file has not been checked yet/)).toBeVisible();
  expect(screen.queryByText(/The original source for this version is unavailable/)).toBeNull();
  expect(state.get.mock.calls.some(([url]) => String(url).includes("current_id=3"))).toBe(false);
  expect(screen.getByRole("button", { name: "Approve version" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Download PDF" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Load saved version source details" }));
  await waitFor(() => expect(reject).toBeDefined());
  expect(screen.getByText("Checking the original file before approval and download...")).toBeVisible();
  await act(async () => reject(new ApiError("Temporary metadata refusal", 503, {})));
  await screen.findByText(/Source file details could not be verified/);
  expect(screen.queryByText(/The original source for this version is unavailable/)).toBeNull();
  expect(screen.getByRole("button", { name: "Approve version" })).toBeDisabled();
  state.get.mockImplementation((url: string) => url === "/resume/page?limit=1&current_id=3"
    ? Promise.resolve({ resumes: [], next_cursor: null, selected_resume: { id: 3, filename: "history.pdf", source_available: true, source_format: "pdf", created_at: stamp } })
    : historicalVersionRead(url));
  fireEvent.click(screen.getByRole("button", { name: "Load saved version source details" }));
  expect(await screen.findByRole("button", { name: "Download PDF" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Approve version" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "I have reviewed this version and its proposed changes." }));
  expect(screen.getByRole("button", { name: "Approve version" })).toBeEnabled();
  expect(state.post).not.toHaveBeenCalled();
});

it.each(["missing-row", "missing-source"])("reports confirmed %s after an explicit historical source lookup", async (outcome) => {
  state.get.mockImplementation((url: string) => {
    if (url !== "/resume/page?limit=1&current_id=3") return historicalVersionRead(url);
    if (outcome === "missing-row") return Promise.reject(new ApiError("Selected resume is unavailable", 404, {}));
    return Promise.resolve({ resumes: [], next_cursor: null, selected_resume: { id: 3, filename: "history.pdf", source_available: false, source_format: null, created_at: stamp } });
  });
  await page();
  fireEvent.click(await screen.findByText("Open version 1 for review"));
  fireEvent.click(await screen.findByText("Review source snapshot"));
  expect(screen.queryByText(/The original source for this version is unavailable/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Load saved version source details" }));
  await waitFor(() => expect(screen.getByText(/The original source for this version is unavailable/)).toBeVisible(), { timeout: 2500 });
  expect(screen.getByRole("button", { name: "Approve version" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Download PDF" })).toBeNull();
});

it("uses aggregate approvals rather than a metadata page prefix, while missing submitted IDs never select another version", async () => {
  const metadata = Array.from({ length: 20 }, (_, index) => ({ id: `meta-${index}`, resume_id: 1, category: "experience", title: `Metadata fact ${index}`, approval_state: "pending", provenance: "resume", confidence: "high", created_at: stamp, updated_at: stamp }));
  const versions = Array.from({ length: 20 }, (_, index) => ({ id: `version-${index}`, resume_id: 1, opportunity_id: state.id, version_number: 100-index, label: `Version metadata ${index}`, approval_state: "draft", created_at: stamp, submitted_at: null }));
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) return Promise.resolve({ opportunity: opportunity(), resumes: { resumes: [{ id: 999, filename: "newest.pdf", source_available: true, source_format: "pdf", created_at: stamp }], selected_resume: { id: 1, filename: "selected-off-page.pdf", source_available: true, source_format: "pdf", created_at: stamp }, next_cursor: "older-resumes" },
      match: null, match_source_state: "none", pages: { resume_versions: { items: versions, next_cursor: "older-versions" }, evidence: { items: metadata, next_cursor: "older-facts" } }, approved_evidence_count: 81, submitted_resume_version_id: "missing-historical-target", submitted_resume_version: null, selected_resume_version: null });
    if (url.includes("/workspace/evidence?")) return Promise.resolve({ items: [{ ...metadata[0], id: "older", title: "One older metadata fact" }], next_cursor: null });
  });
  render(tree());
  await screen.findByRole("heading", { name: "Engineer" });
  await waitFor(() => expect(generate()).toBeEnabled());
  expect(screen.getByText("81 approved evidence items.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Approve evidence" })).toBeNull();
  expect(screen.getAllByRole("button", { name: /^Open Metadata fact .* for review$/ })).toHaveLength(20);
  expect(screen.getByRole("combobox", { name: /Submitted version/ })).toHaveValue("missing-historical-target");
  fireEvent.change(screen.getByRole("combobox", { name: "Application stage" }), { target: { value: "applied" } });
  expect(screen.getByText("Create and select the exact resume version you submitted.")).toBeVisible();
  expect(state.post).not.toHaveBeenCalled();
  expect(state.wire.mock.calls.filter(([url]) => String(url).includes("/workspace?"))).toHaveLength(1);
  expect(state.wire.mock.calls.some(([url]) => String(url).includes("/workspace/evidence/") || String(url).startsWith("/resume/page"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Older evidence" }));
  await screen.findByText("One older metadata fact");
  expect(screen.queryByText("Metadata fact 1")).toBeNull();
  expect(generate()).toBeEnabled();
  expect(state.post).not.toHaveBeenCalled();
});

const savedMatch = { match_id: 91, match_score: 88, grade: "A", required_skills: [], full_matches: [], partial_matches: [], true_gaps: [], skill_verification_rate: 100, dimensions: [], fit_summary: "Owned match for resume one", improvement_tips: [], created_at: stamp, mode: "basic" };
function matchWorkspace(sourceState: "none" | "current" | "different_resume" | "unavailable", match: unknown = null) {
  const current = { id: resumeId, filename: `synthetic-${resumeId}.pdf`, source_available: true, source_format: "pdf", created_at: stamp };
  return { opportunity: { ...opportunity(), latest_match_id: sourceState === "none" ? null : 91 },
    resumes: { resumes: [1, 2].map((id) => ({ ...current, id, filename: `synthetic-${id}.pdf` })), selected_resume: current, next_cursor: null },
    pages: { resume_versions: { items: [], next_cursor: null }, contacts: state.tab === "overview" ? { items: [], next_cursor: null } : null, reminders: state.tab === "overview" ? { items: [], next_cursor: null } : null },
    match, match_source_state: sourceState, submitted_resume_version_id: null, submitted_resume_version: null, selected_resume_version: null, approved_evidence_count: null };
}
async function remoteResumeChange() {
  resumeId = 2;
  await act(async () => { await client.invalidateQueries({ queryKey: ["workspace", "authenticated:fixture-owner", state.id] }); });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Resume" })).toHaveValue("2"));
}
const completedMatchRun = () => ({ id: "run-match-one", opportunity_id: state.id, operation: "job_match", status: "succeeded", estimated_units: 0, committed_units: 0 });
function provideMatchRunReads() {
  const original = state.get.getMockImplementation()!;
  state.get.mockImplementation((url: string, signal?: AbortSignal) => url === "/v1/analysis-runs/run-match-one/result" ? Promise.resolve({ result: savedMatch })
    : url === "/v1/analysis-runs/run-match-one" ? Promise.resolve(completedMatchRun()) : original(url, signal));
}

it.each(["different_resume", "unavailable"] as const)("current-source match refuses a %s score without starting new work", async (sourceState) => {
  state.tab = "overview";
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve(matchWorkspace(sourceState, savedMatch)) : undefined);
  await page();
  expect(screen.queryByText(savedMatch.fit_summary)).toBeNull();
  expect(screen.getByRole("heading", { name: sourceState === "different_resume" ? "No match for this resume" : "Saved match unavailable" })).toBeVisible();
  expect(screen.getByText(sourceState === "different_resume" ? /The saved match belongs to a different resume/ : /The saved match could not be verified for this resume/)).toBeVisible();
  expect(state.post).not.toHaveBeenCalled();
  expect(state.wire.mock.calls.some(([url]) => String(url).includes("/match?"))).toBe(false);
});

it("current-source match learning read is explicit and binds the current resume ID", async () => {
  state.tab = "learning"; enableCreditExecution();
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve({
    ...matchWorkspace(resumeId === 1 ? "current" : "different_resume"), skill_roi: { items: [], opportunity_count: 0 },
  })
    : url === `/v1/opportunities/${state.id}/match?resume_id=1` ? Promise.resolve(savedMatch) : undefined);
  await page();
  expect(state.wire.mock.calls.some(([url]) => String(url).includes("/match?"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Load saved match for learning strategy" }));
  await screen.findByRole("heading", { name: "Build a learning plan for this role" });
  expect(state.wire.mock.calls.filter(([url]) => String(url).includes("/match?")).map(([url]) => url)).toEqual([`/v1/opportunities/${state.id}/match?resume_id=1`]);
  await remoteResumeChange();
  expect(screen.queryByRole("heading", { name: "Build a learning plan for this role" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Load saved match for learning strategy" })).toBeNull();
  expect(state.post).not.toHaveBeenCalled();
});

it.each([false, true])("opens an owned saved curated plan with paid credits paused and funded curated creation %s", async (fundedCurated) => {
  state.tab = "learning";
  const savedPath = `/recommendations/match_strategy/saved?match_id=${savedMatch.match_id}`;
  const summary = "Practice writing reliable SQL queries.";
  state.direct.mockImplementation((url: string) => {
    if (url === "/auth/bootstrap" && fundedCurated) return Promise.resolve(ownedBootstrapFixture({ availability: {
      free_core_operations: ["curated_learning"], free_core_limits: {
        curated_learning: { per_account_30_days: 4, max_input_bytes: 10_000, max_source_records: 100 },
      },
    } }));
    if (url.includes("/workspace?")) return Promise.resolve({
      ...matchWorkspace(resumeId === 1 ? "current" : "different_resume"),
      skill_roi: { items: [], opportunity_count: 0 },
    });
    if (url === `/v1/opportunities/${state.id}/match?resume_id=1`) return Promise.resolve(savedMatch);
    if (url === savedPath) return Promise.resolve({
      match_id: savedMatch.match_id, job_title: "Engineer", company: "Synthetic Company",
      current_score: savedMatch.match_score, readiness_summary: summary, missing_hiring_signals: [],
      learning_priorities: [], project_recommendations: [], timeline: [], generated_by: "curated", provenance: "curated",
    });
  });

  await page();
  expect(state.wire.mock.calls.some(([url]) => String(url).includes("/match?"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Load saved match for learning strategy" }));
  const lookup = await screen.findByRole("button", { name: "Find saved curated plan" });
  expect(state.wire.mock.calls.filter(([url]) => String(url).includes("/match?")).map(([url]) => url))
    .toEqual([`/v1/opportunities/${state.id}/match?resume_id=1`]);
  const create = screen.getByRole("button", { name: "View curated plan" });
  if (fundedCurated) {
    expect(create).toBeEnabled();
    expect(screen.getByText(/0 credits within an allowance of 4 new plans/)).toBeVisible();
  } else {
    expect(create).toBeDisabled();
  }
  expect(screen.queryByRole("combobox", { name: "Learning plan mode" })).not.toBeInTheDocument();
  expect(state.wire.mock.calls.some(([url]) => url === savedPath)).toBe(false);
  fireEvent.click(lookup);
  expect(await screen.findByText(summary)).toBeVisible();
  expect(screen.getByText("Saved curated plan · no new credits charged.")).toBeVisible();
  expect(state.wire.mock.calls.filter(([url]) => url === savedPath)).toEqual([[savedPath, expect.any(AbortSignal)]]);
  expect(state.post).not.toHaveBeenCalled();

  await remoteResumeChange();
  expect(screen.queryByText(summary)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Find saved curated plan" })).not.toBeInTheDocument();
  expect(state.wire.mock.calls.filter(([url]) => url === savedPath)).toHaveLength(1);
});

it("asks before loading the next exact saved match when both résumés have a match", async () => {
  state.tab = "learning";
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) {
      const workspace = matchWorkspace("current");
      return Promise.resolve({ ...workspace, opportunity: { ...workspace.opportunity, latest_match_id: resumeId === 1 ? 91 : 92 },
        skill_roi: { items: [], opportunity_count: 0 } });
    }
    if (url.endsWith("/match?resume_id=1")) return Promise.resolve(savedMatch);
    if (url.endsWith("/match?resume_id=2")) return Promise.resolve({ ...savedMatch, match_id: 92 });
  });
  const matches = () => state.wire.mock.calls.map(([url]) => url).filter(url => url.includes("/match?"));
  await page();
  fireEvent.click(screen.getByRole("button", { name: "Load saved match for learning strategy" }));
  await screen.findByRole("button", { name: "Find saved curated plan" });
  expect(matches()).toEqual([`/v1/opportunities/${state.id}/match?resume_id=1`]);
  fireEvent.change(screen.getByRole("combobox", { name: "Resume" }), { target: { value: "2" } });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Resume" })).toHaveValue("2"));
  const load = await screen.findByRole("button", { name: "Load saved match for learning strategy" });
  expect(matches()).toEqual([`/v1/opportunities/${state.id}/match?resume_id=1`]);
  expect(screen.queryByRole("button", { name: "Find saved curated plan" })).not.toBeInTheDocument();
  fireEvent.click(load);
  await screen.findByRole("button", { name: "Find saved curated plan" });
  expect(matches()).toEqual([`/v1/opportunities/${state.id}/match?resume_id=1`, `/v1/opportunities/${state.id}/match?resume_id=2`]);
  expect(state.post).not.toHaveBeenCalled();
});

it.each([503, 409])("retries a saved match after HTTP %s using only the needed reads", async (status) => {
  state.tab = "learning";
  const matchPath = `/v1/opportunities/${state.id}/match?resume_id=1`;
  let matchReads = 0;
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) return Promise.resolve({ ...matchWorkspace("current"),
      skill_roi: { items: [], opportunity_count: 0 } });
    if (url === matchPath) return ++matchReads === 1
      ? Promise.reject(new ApiError("Saved match unavailable", status, null))
      : Promise.resolve(savedMatch);
  });
  await page();
  fireEvent.click(screen.getByRole("button", { name: "Load saved match for learning strategy" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry saved match" }));
  await screen.findByRole("button", { name: "Find saved curated plan" });
  expect(matchReads).toBe(2);
  expect(state.wire.mock.calls.filter(([url]) => url.includes("/workspace?"))).toHaveLength(status === 409 ? 2 : 1);
});

it("never retries an old match after conflict readback confirms another résumé", async () => {
  state.tab = "learning";
  let workspaceReads = 0;
  const matchPath = `/v1/opportunities/${state.id}/match?resume_id=1`;
  state.direct.mockImplementation((url: string) => {
    if (url.includes("/workspace?")) {
      if (workspaceReads++ > 0) resumeId = 2;
      return Promise.resolve({ ...matchWorkspace(resumeId === 1 ? "current" : "different_resume"),
        skill_roi: { items: [], opportunity_count: 0 } });
    }
    if (url === matchPath) return Promise.reject(new ApiError("Saved match uses another résumé", 409, null));
  });
  await page();
  fireEvent.click(screen.getByRole("button", { name: "Load saved match for learning strategy" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry saved match" }));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Resume" })).toHaveValue("2"));
  expect(state.wire.mock.calls.filter(([url]) => url.includes("/match?"))).toEqual([[matchPath, expect.any(AbortSignal)]]);
  expect(screen.queryByRole("button", { name: "Find saved curated plan" })).not.toBeInTheDocument();
});

const rankedSkill = { opportunity_count: 1, items: [{ skill: "postgresql", opportunity_count: 1,
  demand_ratio: 1, evidence_strength: 0, outcome_signal: 0, estimated_hours: 30, score: 93,
  reason: "Appears in the current owned opportunity." }] };

it("uses the learning Workspace projection without a standalone Skill ROI request", async () => {
  state.tab = "learning";
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve({
    ...matchWorkspace("none"), skill_roi: rankedSkill,
  }) : undefined);
  await page();
  expect(await screen.findByRole("heading", { name: "postgresql" })).toBeVisible();
  expect(state.wire.mock.calls.map(([url]) => url)).toEqual(expect.arrayContaining([
    `/v1/opportunities/${state.id}/workspace?tab=learning&limit=20&include_skill_roi=true`,
  ]));
  expect(state.wire.mock.calls.filter(([url]) => String(url).includes("/workspace?"))).toHaveLength(1);
  expect(state.wire.mock.calls.some(([url]) => url === "/v1/skill-roi")).toBe(false);
});

it("loads the standalone result only when the owned Workspace response is from an older backend", async () => {
  state.tab = "learning";
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve(matchWorkspace("none"))
    : url === "/v1/skill-roi" ? Promise.resolve(rankedSkill) : undefined);
  await page();
  expect(await screen.findByRole("heading", { name: "postgresql" })).toBeVisible();
  expect(state.wire.mock.calls.filter(([url]) => url === "/v1/skill-roi")).toHaveLength(1);
});

it("never uses missing ROI as a fallback hint on a mismatched Workspace response", async () => {
  state.tab = "learning";
  const response = matchWorkspace("none");
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve({
    ...response, opportunity: { ...response.opportunity, id: "other-opportunity" },
  }) : undefined);
  await page();
  expect(await screen.findByText("Skill ROI is temporarily unavailable.")).toBeVisible();
  expect(state.wire.mock.calls.some(([url]) => url === "/v1/skill-roi")).toBe(false);
});

it.each([null, { items: [] }, { opportunity_count: 0, items: [{ skill: "unverified" }] }])(
  "reports an unavailable or malformed current projection without a compatibility read",
  async (skill_roi) => {
    state.tab = "learning";
    state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve({
      ...matchWorkspace("none"), skill_roi,
    }) : undefined);
    await page();
    expect(await screen.findByText("Skill ROI is temporarily unavailable.")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Skill ROI needs match data" })).toBeNull();
    expect(state.wire.mock.calls.some(([url]) => url === "/v1/skill-roi")).toBe(false);
  },
);

it.each([401, 404, 429, 503])("never attempts standalone Skill ROI after Workspace HTTP %s", async (status) => {
  state.tab = "learning";
  state.direct.mockImplementation((url: string) => url.includes("/workspace?")
    ? Promise.reject(new ApiError("Workspace unavailable", status, null)) : undefined);
  render(tree());
  expect(await screen.findByRole("heading", { name: "Opportunity unavailable" })).toBeVisible();
  expect(state.wire.mock.calls.some(([url]) => url === "/v1/skill-roi")).toBe(false);
  expect(state.wire.mock.calls.filter(([url]) => String(url).includes("/workspace?"))).toHaveLength(1);
});

it("current-source match captures a legacy request before awaiting its late response", async () => {
  state.tab = "overview";
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve(matchWorkspace("none")) : undefined);
  provideMatchRunReads();
  let finish!: (value: unknown) => void;
  state.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  await page();
  fireEvent.click(screen.getAllByRole("button", { name: "Run match" })[0]);
  await waitFor(() => expect(requests()).toHaveLength(1));
  expect(requests()[0][1].input.resume_id).toBe(1);
  await remoteResumeChange();
  await act(async () => finish(completedMatchRun()));
  await screen.findByText(/Your earlier match request belongs to another resume selection/);
  expect(screen.queryByText(savedMatch.fit_summary)).toBeNull();
  expect(state.wire.mock.calls.some(([url]) => String(url).endsWith("/run-match-one/result"))).toBe(false);
  expect(requests()).toHaveLength(1);
});

it("current-source match hides a cached legacy result after a remote resume switch", async () => {
  state.tab = "overview";
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve(matchWorkspace("none")) : undefined);
  provideMatchRunReads(); state.post.mockResolvedValue(completedMatchRun());
  await page(); fireEvent.click(screen.getAllByRole("button", { name: "Run match" })[0]);
  await screen.findByText(savedMatch.fit_summary);
  await remoteResumeChange();
  expect(screen.queryByText(savedMatch.fit_summary)).toBeNull();
  expect(screen.getByText(/Your earlier match request belongs to another resume selection/)).toBeVisible();
  expect(state.wire.mock.calls.filter(([url]) => String(url).endsWith("/run-match-one/result"))).toHaveLength(1);
  expect(requests()).toHaveLength(1);
});

it("current-source match does not bind a late approved quote response to a newer resume", async () => {
  state.tab = "overview"; enableCreditExecution();
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve(matchWorkspace("none")) : undefined);
  provideMatchRunReads();
  const original = state.post.getMockImplementation()!;
  let finish!: (value: unknown) => void;
  state.post.mockImplementation((url, ...rest) => url === "/v1/analysis-runs" ? new Promise((resolve) => { finish = resolve; }) : original(url, ...rest));
  await page(); fireEvent.change(screen.getByRole("combobox", { name: /Match mode/ }), { target: { value: "enhanced" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Run match" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "Approve 8 credits" }));
  await waitFor(() => expect(requests()).toHaveLength(1));
  expect(requests()[0][1].input.resume_id).toBe(1);
  await remoteResumeChange(); await act(async () => finish(completedMatchRun()));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Approve 8 credits" })).toBeNull());
  expect(screen.queryByText(savedMatch.fit_summary)).toBeNull();
  expect(state.wire.mock.calls.some(([url]) => String(url).endsWith("/run-match-one/result"))).toBe(false);
  expect(requests()).toHaveLength(1);
});

it("current-source match keeps quoted reuse bound to its original resume after caching the result", async () => {
  state.tab = "overview"; enableCreditExecution();
  state.direct.mockImplementation((url: string) => url.includes("/workspace?") ? Promise.resolve(matchWorkspace("none")) : undefined);
  provideMatchRunReads(); state.post.mockResolvedValue({ reused_run: completedMatchRun() });
  await page(); fireEvent.change(screen.getByRole("combobox", { name: /Match mode/ }), { target: { value: "enhanced" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Run match" })[0]);
  await screen.findByText(savedMatch.fit_summary);
  expect(state.wire.mock.calls.filter(([url]) => url === "/auth/bootstrap")).toHaveLength(1);
  expect(state.wire.mock.calls.filter(([url]) => url.includes("/workspace?"))).toHaveLength(1);
  expect(trackEvent).not.toHaveBeenCalledWith("analysis_completed", expect.anything());
  expect(trackEvent).not.toHaveBeenCalledWith("first_useful_match", expect.anything());
  await remoteResumeChange();
  expect(screen.queryByText(savedMatch.fit_summary)).toBeNull();
  expect(screen.getByText(/Your earlier match request belongs to another resume selection/)).toBeVisible();
  expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/credits/analysis-reuse"]);
  expect(state.wire.mock.calls.filter(([url]) => String(url).endsWith("/run-match-one/result"))).toHaveLength(1);
});
