import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), push: vi.fn() }));
vi.mock("../../lib/api", () => ({ apiGet: api.get, apiPostJson: api.post }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: api.push }) }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
import Workspace from "./page";

let client: QueryClient;
afterEach(() => { cleanup(); client?.clear(); vi.resetAllMocks(); });
it("creates an opportunity with the explicitly chosen older resume without fetching all pages", async () => {
  const user = userEvent.setup();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const resume = (id: number) => ({ id, filename: `owned-${id}.pdf`, created_at: "2026-11-01T00:00:00Z", source_available: true, source_format: "pdf" });
  api.get.mockImplementation(async (path: string) => path.startsWith("/v1/opportunities?") ? { items: [], total: 0 }
    : path.includes("cursor=") ? { resumes: [resume(1)], next_cursor: null, selected_resume: null }
    : { resumes: [resume(20)], next_cursor: "older", selected_resume: null });
  api.post.mockResolvedValue({ id: "owned_opportunity", source: "manual", resume_id: 1 });
  render(<QueryClientProvider client={client}><Workspace /></QueryClientProvider>);
  await user.click(screen.getByRole("button", { name: "Add opportunity" }));
  await screen.findByRole("option", { name: "owned-20.pdf · #20" });
  expect(api.get.mock.calls.filter(([path]) => String(path).startsWith("/resume/page"))).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Load older resumes" }));
  await screen.findByRole("option", { name: "owned-1.pdf · #1" });
  await user.selectOptions(screen.getByRole("combobox", { name: "Resume" }), "1");
  await user.type(screen.getByRole("textbox", { name: "Role title" }), "Software Engineer");
  await user.type(screen.getByRole("textbox", { name: "Job description" }), "A complete synthetic role description for an owned local test.");
  await user.click(screen.getByRole("button", { name: "Create workspace" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledExactlyOnceWith("/v1/opportunities", expect.objectContaining({ resume_id: 1 })));
  await waitFor(() => expect(api.push).toHaveBeenCalledExactlyOnceWith("/workspace/owned_opportunity"));
});
