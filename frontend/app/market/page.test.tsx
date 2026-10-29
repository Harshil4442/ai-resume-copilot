import { ownedBootstrapFixture } from "../../lib/fixtures/ownedBootstrap";
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
    : path === "/auth/bootstrap" ? ownedBootstrapFixture({ availability: { catalog_enabled: true, execution_enabled: true, supported_operations: ["market_report"] } })
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

it("finds unchanged saved reports while new paid reports are unavailable without requesting a quote", async () => {
  state.get.mockImplementation(async (path) => path === "/resume/list" ? { resumes: [{ id: 1, filename: "selected.pdf" }] }
    : path === "/auth/bootstrap" ? ownedBootstrapFixture({ availability: { catalog_enabled: true, execution_enabled: false, supported_operations: [] } })
    : { id: "saved-market", operation: "market_analysis", status: "cancelled", billing_currency: "credits", committed_units: 0, usage_state: "released" });
  state.post.mockResolvedValue({ reused_run: { id: "saved-market", operation: "market_analysis", status: "succeeded" } });
  render(<QueryClientProvider client={client}><MarketPage /></QueryClientProvider>);
  await screen.findByText("Market reports are not currently available for credit execution.");
  expect(screen.getByRole("button", { name: "Review market report charge" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Find saved result for Market report · no charge" }));
  await screen.findByText("Your existing request was loaded. No new credits were charged.");
  expect(state.post.mock.calls.map(([path]) => path)).toEqual(["/v1/credits/analysis-reuse"]);
  await waitFor(() => expect(state.get).toHaveBeenCalledWith("/v1/analysis-runs/saved-market", expect.any(AbortSignal)));
});

it("a saved-result lookup never starts new work when no report exists", async () => {
  render(<QueryClientProvider client={client}><MarketPage /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Find saved result for Market report · no charge" }));
  await screen.findByText("No saved result matches these inputs. No work was started or credits charged.");
  expect(state.post.mock.calls.map(([path]) => path)).toEqual(["/v1/credits/analysis-reuse"]);
  expect(screen.queryByRole("button", { name: "Approve 1 credit" })).not.toBeInTheDocument();
});
