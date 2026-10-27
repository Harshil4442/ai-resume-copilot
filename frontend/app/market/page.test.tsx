import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../../lib/api", () => ({ apiGet: state.get, apiPostJson: state.post, ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
import MarketPage from "./page";
let client: QueryClient;
const quote = { schema_version: "unified-service-quote-v1", quote_id: "csq_market", operation: "market_report", input_fingerprint: "c".repeat(64),
  route_id: "market-native", route_version: "1", mode: "native", tariff_version: "credit-services-v1", credits: 1,
  deliverable: "A sample-based market demand report.", supported_limits: { max_jobs: 50 },
  created_at: "2026-10-27T10:00:00Z", expires_at: "2026-10-27T10:15:00Z", server_time: "2026-10-27T10:00:00Z" };
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  state.get.mockReset().mockImplementation(async (path) => path === "/resume/list" ? { resumes: [{ id: 1, filename: "selected.pdf" }] }
    : path === "/v1/credits/availability" ? { catalog_enabled: true, execution_enabled: true, supported_operations: ["market_report"] }
    : { id: "run-market", operation: "market_analysis", status: "cancelled", billing_currency: "credits", committed_units: 0, usage_state: "released" });
  state.post.mockReset().mockImplementation(async (path) => path === "/v1/credits/analysis-reuse" ? { reused_run: null }
    : path === "/v1/credits/service-quotes" ? quote : { id: "run-market", operation: "market_analysis", status: "queued" });
});
afterEach(() => { cleanup(); client.clear(); });
async function showReview() {
  render(<QueryClientProvider client={client}><MarketPage /></QueryClientProvider>);
  const button = await screen.findByRole("button", { name: "Review market report charge" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  return screen.findByRole("button", { name: "Approve 1 credit" });
}
it("shows native report terms and exact query before starting an asynchronous report", async () => {
  const approve = await showReview(); expect(screen.getByText("No generative AI call in this service")).toBeVisible();
  expect(screen.getByText("selected.pdf", { selector: "dd" })).toBeVisible();
  expect(state.post.mock.calls.some(([path]) => path === "/v1/analysis-runs" || path === "/market/analyze")).toBe(false);
  fireEvent.click(approve);
  await waitFor(() => expect(state.post.mock.calls.some(([path]) => path === "/v1/analysis-runs")).toBe(true));
  const execution = state.post.mock.calls.find(([path]) => path === "/v1/analysis-runs")!;
  expect(execution[1]).toMatchObject({ operation: "market_analysis", accepted_service_quote_id: "csq_market", input: { target_role: "Software Engineer", resume_id: 1, max_results: 50 } });
  expect(state.post.mock.calls.some(([path]) => path === "/market/analyze")).toBe(false);
});
it("requires a fresh review if the market query changes", async () => {
  await showReview(); fireEvent.change(screen.getByRole("textbox", { name: "Target role" }), { target: { value: "Data scientist" } });
  expect(screen.getByRole("button", { name: "Approve 1 credit" })).toBeDisabled();
  expect(state.post.mock.calls.some(([path]) => path === "/v1/analysis-runs")).toBe(false);
});
