import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CreditAvailability } from "../../lib/unifiedServiceQuotes";
const state = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("../../lib/api", () => ({ apiPostJson: state.post,
  ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
import { ApiError } from "../../lib/api";
import { ResumeSkillEnrichment } from "./ResumeSkillEnrichment";
import { ResumeBulletSuggestions } from "./ResumeBulletSuggestions";
import { MatchQuestionPanel } from "./MatchQuestionPanel";
import { LearningStrategyPanel } from "./LearningStrategyPanel";
const availability: CreditAvailability = { catalog_enabled: true, execution_enabled: true,
  supported_operations: ["resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"], free_core_operations: ["curated_learning"],
  free_core_limits: { curated_learning: { per_account_30_days: 4, max_input_bytes: 10000, max_source_records: 100 } } };
const resume = { id: 1, filename: "selected.pdf" }, job = { id: "opp_one", title: "Engineer", employer: "Example", description: "Build reliable services with Python." };
const enrichment = { resume_id: 1, skills: ["Python", "Django"], enrichment_state: "completed" };
const billing = { run_id: "run_sync", billing_currency: "credits", committed_credits: 2, charged_this_request: 2, reused: false };
const answer = { answer: "Python is listed in the approved resume.", confidence: "high", suggested_followups: [], sources: ["match"], mode: "direct", provenance: "stored_data", credit_execution: null };
const learning = { match_id: 5, job_title: "Engineer", company: "Example", current_score: 70, readiness_summary: "Practice the missing database skills.",
  missing_hiring_signals: [{ signal: "Databases", why_it_matters: "Role requirement", severity: "medium" }],
  learning_priorities: [{ skill: "SQL", priority: "high", current_status: "gap", reason: "Required by role", expected_outcome: "Build a query", resources: [{ title: "SQL guide", platform: "PostgreSQL", url: "https://postgresql.org/docs/", skill: "SQL" }] }],
  project_recommendations: [{ title: "Practice database", covers_gaps: ["SQL"], description: "Future practice", implementation_steps: ["Design tables", "Write queries"], resume_bullets: [], interview_talking_points: [] }],
  timeline: [{ phase: "Week 1", focus: "SQL", deliverable: "Working queries" }], generated_by: "curated", provenance: "curated", warnings: [] };
let client: QueryClient;
function view(node: React.ReactNode) { return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>); }
function makeQuote(operation: string) { return { schema_version: "unified-service-quote-v1", quote_id: "csq_sync", operation, input_fingerprint: "d".repeat(64), route_id: "sync-qualified", route_version: "1", mode: "enhanced", tariff_version: "v1", credits: 2,
  deliverable: "A validated source-grounded deliverable.", supported_limits: { max_pages: 2 }, created_at: "2026-10-27T10:00:00Z", expires_at: "2026-10-27T10:15:00Z", server_time: "2026-10-27T10:00:00Z" }; }
beforeEach(() => { client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  state.post.mockReset().mockImplementation(async (url, body) => {
    if (url === "/v1/credits/synchronous-reuse") return { reused_run: null, result: null };
    if (url === "/v1/credits/synchronous-service-quotes") return makeQuote(body.operation);
    if (url === "/resume/1/enrich") return { ...enrichment, credit_execution: billing };
    if (url === "/rag/ask") return answer;
    if (url === "/recommendations/match_strategy") return learning;
    if (url === "/llm/rewrite_bullets") return { summary: "Clarified the action.", rewritten_bullets: ["Built reliable services."], evidence_links: [{ original_text: "Worked on services.", replacement_text: "Built reliable services.", evidence_ids: ["evi_one"] }], credit_execution: billing };
    throw new Error(`Unexpected ${url}`);
  }); });
afterEach(() => { cleanup(); client.clear(); });
async function requestEnrichment(onResult = vi.fn()) { view(<ResumeSkillEnrichment resume={resume} availability={availability} onResult={onResult} />); fireEvent.click(screen.getByRole("button", { name: "Review skill enrichment charge" })); return screen.findByRole("button", { name: "Approve 2 credits" }); }
it("separates saved resume enrichment from upload and requires explicit price approval", async () => {
  const receive = vi.fn(); fireEvent.click(await requestEnrichment(receive));
  await waitFor(() => expect(receive).toHaveBeenCalledWith(enrichment));
  const execution = state.post.mock.calls.find(([url]) => url === "/resume/1/enrich")!;
  expect(execution[1]).toEqual({ resume_id: 1, accepted_service_quote_id: "csq_sync", idempotency_key: expect.any(String) });
  expect(screen.getByText("2 credits charged for this delivery.")).toBeVisible();
});
it("reuses an exact saved enrichment without a quote or fresh charge", async () => {
  state.post.mockResolvedValue({ reused_run: { id: "run_sync", operation: "sync_resume_enrichment", status: "succeeded" }, result: { ...enrichment, credit_execution: { ...billing, reused: true, charged_this_request: 0 } } });
  const receive = vi.fn(); view(<ResumeSkillEnrichment resume={resume} availability={availability} onResult={receive} />);
  fireEvent.click(screen.getByRole("button", { name: "Review skill enrichment charge" }));
  await waitFor(() => expect(receive).toHaveBeenCalledWith(enrichment)); expect(state.post).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Saved result reused · 0 new credits charged/)).toBeVisible();
});
it("checks the original key after an uncertain result without re-dispatching", async () => {
  const original = state.post.getMockImplementation()!;
  state.post.mockImplementation((url, body, headers) => url === "/resume/1/enrich" ? Promise.reject(new TypeError("Response lost"))
    : url === "/v1/credits/synchronous-reuse" && headers ? Promise.resolve({ reused_run: { id: "run_sync", operation: "sync_resume_enrichment", status: "succeeded" }, result: { ...enrichment, credit_execution: { ...billing, reused: true, charged_this_request: 0 } } }) : original(url, body, headers));
  fireEvent.click(await requestEnrichment()); const check = await screen.findByRole("button", { name: "Check approved request status" }); await waitFor(() => expect(check).toBeEnabled()); fireEvent.click(check);
  await screen.findByText(/Saved result reused · 0 new credits charged/);
  const calls = state.post.mock.calls.filter(([url]) => url === "/resume/1/enrich"); expect(calls).toHaveLength(1);
  expect(state.post.mock.calls.find(([url, , headers]) => url === "/v1/credits/synchronous-reuse" && headers)?.[2]).toEqual({ "Idempotency-Key": calls[0][1].idempotency_key });
});
it("permits a fresh review after an explicit pre-work refusal", async () => {
  const original = state.post.getMockImplementation()!;
  state.post.mockImplementation((url, ...rest) => url === "/resume/1/enrich" ? Promise.reject(new ApiError("Quote expired", 409, { detail: { code: "synchronous_service_rejected", run_created: false } })) : original(url, ...rest));
  fireEvent.click(await requestEnrichment()); await screen.findByText("Quote expired"); expect(screen.getByRole("button", { name: "Approve 2 credits" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh service quote" })); await waitFor(() => expect(screen.getByRole("button", { name: "Approve 2 credits" })).toBeEnabled());
  expect(state.post.mock.calls.filter(([url]) => url === "/resume/1/enrich")).toHaveLength(1);
});
it("shows grounded original and suggested bullets without modifying a source file", async () => {
  view(<ResumeBulletSuggestions resume={resume} job={job} availability={availability} approvedEvidence />);
  fireEvent.click(screen.getByRole("button", { name: "Review bullet suggestion charge" })); fireEvent.click(await screen.findByRole("button", { name: "Approve 2 credits" }));
  await screen.findByText("Built reliable services."); expect(screen.getByText("Worked on services.")).toBeVisible();
  expect(state.post.mock.calls.filter(([url]) => !url.includes("/credits/")).map(([url]) => url)).toEqual(["/llm/rewrite_bullets"]);
});
it("uses the free stored-data answer before considering an enhanced quote", async () => {
  view(<MatchQuestionPanel resume={resume} job={job} matchId={5} availability={availability} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: "Which skills match?" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Answer mode" }), { target: { value: "enhanced" } }); fireEvent.click(screen.getByRole("button", { name: "Ask question" }));
  await screen.findByText(answer.answer); expect(state.post).toHaveBeenCalledExactlyOnceWith("/rag/ask", expect.objectContaining({ mode: "basic" }));
  expect(screen.getByText("Stored facts · 0 credits")).toBeVisible();
});
it("keeps curated learning free and shows source links and future projects", async () => {
  view(<LearningStrategyPanel resume={resume} job={job} matchId={5} availability={availability} />);
  fireEvent.click(screen.getByRole("button", { name: "View curated plan" })); await screen.findByText(learning.readiness_summary);
  expect(screen.getByRole("link", { name: "SQL guide" })).toHaveAttribute("href", "https://postgresql.org/docs/");
  expect(state.post).toHaveBeenCalledExactlyOnceWith("/recommendations/match_strategy", { match_id: 5, mode: "curated" });
});
it("does not attach a late enrichment result to another selected resume", async () => {
  let finish!: (value: unknown) => void; const original = state.post.getMockImplementation()!;
  state.post.mockImplementation((url, ...rest) => url === "/resume/1/enrich" ? new Promise((resolve) => { finish = resolve; }) : original(url, ...rest));
  const receive = vi.fn(); const rendered = view(<ResumeSkillEnrichment resume={resume} availability={availability} onResult={receive} />);
  fireEvent.click(screen.getByRole("button", { name: "Review skill enrichment charge" })); fireEvent.click(await screen.findByRole("button", { name: "Approve 2 credits" }));
  await waitFor(() => expect(finish).toBeDefined()); rendered.rerender(<QueryClientProvider client={client}><ResumeSkillEnrichment resume={{ id: 2, filename: "second.pdf" }} availability={availability} onResult={receive} /></QueryClientProvider>);
  await act(async () => finish({ ...enrichment, credit_execution: billing })); expect(receive).not.toHaveBeenCalled();
});


it("keeps saved results accessible while new credit execution is paused", async () => {
  state.post.mockResolvedValue({ reused_run: { id: "run_sync", operation: "sync_resume_enrichment", status: "succeeded" }, result: { ...enrichment, credit_execution: { ...billing, reused: true, charged_this_request: 0 } } });
  const receive = vi.fn(); view(<ResumeSkillEnrichment resume={resume} availability={{ ...availability, execution_enabled: false, supported_operations: [] }} onResult={receive} />);
  expect(screen.getByRole("button", { name: "Review skill enrichment charge" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Find saved result · no charge" }));
  await waitFor(() => expect(receive).toHaveBeenCalledWith(enrichment));
  expect(state.post.mock.calls.map(([url]) => url)).toEqual(["/v1/credits/synchronous-reuse"]);
});
