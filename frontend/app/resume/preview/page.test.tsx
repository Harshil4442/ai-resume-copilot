import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { apiBlob, apiDownload, apiGet } from "../../../lib/api";
import { fetchDirectResumeSource, saveVerifiedResumeSource } from "../../../lib/resumeSource";
import Preview from "./page";

let owner = "1";
vi.mock("next-auth/react", () => ({ useSession: () => ({ data: { user: { id: owner } }, status: "authenticated" }) }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("resume=7") }));
vi.mock("../../../lib/api", () => ({ apiGet: vi.fn(), apiDownload: vi.fn(), apiBlob: vi.fn() }));
vi.mock("../../../lib/career", () => ({ fetchResumeSource: vi.fn(), fetchResumeVersionPdf: vi.fn() }));
vi.mock("../../../lib/resumeSource", () => ({
  directResumeSourceEnabled: () => process.env.NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED === "true",
  fetchDirectResumeSource: vi.fn(), saveVerifiedResumeSource: vi.fn(),
}));

const source = { blob: new Blob(["synthetic-docx"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), filename: "original.docx" };
function tree() { return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Preview /></QueryClientProvider>; }
async function page() {
  const rendered = render(tree()); await screen.findByRole("button", { name: "Download original DOCX" }); return rendered;
}

beforeEach(() => {
  owner = "1"; vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "true");
  vi.mocked(apiGet).mockResolvedValue({ resumes: [
    { id: 7, filename: "original.docx", source_available: true, source_format: "docx" },
    { id: 8, filename: "second.docx", source_available: true, source_format: "docx" },
  ] });
  vi.mocked(fetchDirectResumeSource).mockResolvedValue(source);
  vi.mocked(apiBlob).mockResolvedValue(source.blob);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllEnvs(); });

it("downloads verified original bytes without the BFF source byte route", async () => {
  await page(); fireEvent.click(screen.getByRole("button", { name: "Download original DOCX" }));
  await waitFor(() => expect(saveVerifiedResumeSource).toHaveBeenCalledOnce());
  expect(fetchDirectResumeSource).toHaveBeenCalledWith(7, expect.any(AbortSignal), "docx");
  expect(apiDownload).not.toHaveBeenCalled(); expect(apiBlob).not.toHaveBeenCalled();
});

it("default-disabled original download retains the old transport", async () => {
  vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "false");
  await page(); fireEvent.click(screen.getByRole("button", { name: "Download original DOCX" }));
  await waitFor(() => expect(apiDownload).toHaveBeenCalledWith("/resume/7/source", "original.docx"));
  expect(fetchDirectResumeSource).not.toHaveBeenCalled();
});

it("only helper's exact missing binding null uses legacy bytes", async () => {
  vi.mocked(fetchDirectResumeSource).mockResolvedValue(null);
  await page(); fireEvent.click(screen.getByRole("button", { name: "Download original DOCX" }));
  await waitFor(() => expect(saveVerifiedResumeSource).toHaveBeenCalledOnce());
  expect(apiBlob).toHaveBeenCalledWith("/resume/7/source", expect.any(AbortSignal));
});

it("direct denial is visible and never retries or falls back", async () => {
  vi.mocked(fetchDirectResumeSource).mockRejectedValue(new Error("Could not verify the original resume file."));
  await page(); fireEvent.click(screen.getByRole("button", { name: "Download original DOCX" }));
  await screen.findByRole("alert");
  expect(fetchDirectResumeSource).toHaveBeenCalledOnce(); expect(apiBlob).not.toHaveBeenCalled(); expect(apiDownload).not.toHaveBeenCalled();
});

it.each(["selection", "owner", "unmount"])("%s change aborts a late download before saving bytes", async (change) => {
  let finish!: (file: typeof source) => void;
  vi.mocked(fetchDirectResumeSource).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const rendered = await page(); fireEvent.click(screen.getByRole("button", { name: "Download original DOCX" }));
  await waitFor(() => expect(fetchDirectResumeSource).toHaveBeenCalledOnce());
  const signal = vi.mocked(fetchDirectResumeSource).mock.calls[0][1]!;
  if (change === "selection") fireEvent.change(screen.getByRole("combobox"), { target: { value: "8" } });
  if (change === "owner") { owner = "2"; rendered.rerender(tree()); }
  if (change === "unmount") rendered.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { finish(source); });
  expect(saveVerifiedResumeSource).not.toHaveBeenCalled();
});
