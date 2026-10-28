import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("./api", () => ({
  apiPostJson: state.post,
  apiGet: state.get,
  ApiError: class extends Error {},
}));

import { useQuotedAnalysis } from "./useQuotedAnalysis";

const request = { operation: "resume_tailor" as const, opportunity_id: "opp_saved", input: {} };
const availability = {
  catalog_enabled: true, execution_enabled: true,
  supported_operations: ["resume_tailor" as const],
  free_core_operations: [], free_core_limits: {},
};
const quote = {
  schema_version: "unified-service-quote-v1", quote_id: "csq_new",
  operation: "resume_tailor", input_fingerprint: "a".repeat(64),
  route_id: "route_v7", route_version: "v7", mode: "enhanced",
  tariff_version: "credit-services-2026-10-27-v1", credits: 8,
  deliverable: "A complete reviewed tailored resume.", supported_limits: {},
  created_at: "2026-10-29T10:00:00Z", expires_at: "2026-10-29T10:10:00Z",
  server_time: "2026-10-29T10:00:00Z",
};

let client: QueryClient;
function Harness() {
  const action = useQuotedAnalysis({
    request, selectionKey: "one", context: { job: { id: "opp_saved", title: "Engineer" } },
    availability, onRun: vi.fn(),
  });
  return <>
    <button onClick={action.requestQuote}>Review new tailoring</button>
    <button onClick={action.loadSaved}>Load saved tailoring</button>
    {action.review && <span>New quote ready</span>}
  </>;
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  state.post.mockReset().mockImplementation(async (path: string) =>
    path === "/v1/credits/service-quotes" ? quote : { reused_run: null });
  state.get.mockReset();
});
afterEach(() => { cleanup(); client.clear(); });

it("new tailoring checks only the v7 reuse identity before quoting", async () => {
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Review new tailoring" }));
  await screen.findByText("New quote ready");
  expect(state.post.mock.calls.map(([path]) => path)).toEqual([
    "/v1/credits/analysis-reuse", "/v1/credits/service-quotes",
  ]);
});

it("explicit saved-result lookup can include legacy v6 tailoring", async () => {
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Load saved tailoring" }));
  await waitFor(() => expect(state.post).toHaveBeenCalledTimes(1));
  expect(state.post.mock.calls[0][0]).toBe(
    "/v1/credits/analysis-reuse?include_legacy=true",
  );
});
