import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { apiBlob, apiDownload, apiGet } from "../../../lib/api";
import { fetchResumeSource } from "../../../lib/career";
import { fetchDirectResumeSource, saveVerifiedResumeSource } from "../../../lib/resumeSource";
import Preview from "./page";

let owner = "1";
let query = "resume=7";
vi.mock("next-auth/react", () => ({ useSession: () => ({ data: { user: { id: owner } }, status: "authenticated" }) }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(query) }));
vi.mock("../../../lib/api", () => ({ apiGet: vi.fn(), apiDownload: vi.fn(), apiBlob: vi.fn() }));
vi.mock("../../../lib/career", () => ({ fetchResumeSource: vi.fn(), fetchResumeVersionPdf: vi.fn() }));
vi.mock("../../../lib/resumeSource", () => ({
  directResumeSourceEnabled: () => process.env.NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED === "true",
  fetchDirectResumeSource: vi.fn(), saveVerifiedResumeSource: vi.fn(),
}));

const source = { blob: new Blob(["synthetic-docx"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), filename: "original.docx" };
function tree(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) { return <QueryClientProvider client={client}><Preview /></QueryClientProvider>; }
async function page() {
  const rendered = render(tree()); await screen.findByRole("button", { name: "Download original DOCX" }); return rendered;
}

beforeEach(() => {
  owner = "1"; query = "resume=7"; vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "true");
  vi.mocked(apiGet).mockResolvedValue({ resumes: [
    { id: 7, filename: "original.docx", source_available: true, source_format: "docx" },
    { id: 8, filename: "second.docx", source_available: true, source_format: "docx" },
  ] });
  vi.mocked(fetchDirectResumeSource).mockResolvedValue(source);
  vi.mocked(apiBlob).mockResolvedValue(source.blob);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each(["docx", "pdf"])("does not expose or request a cached %s while the explicitly requested TeX resume is refreshing", async (format) => {
  query = "resume=31";
  const cached = { id: 30, filename: `previous.${format}`, source_available: true, source_format: format };
  const requested = { id: 31, filename: "requested.tex", source_available: true, source_format: "tex" };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });
  client.setQueryData(["resumes", "authenticated:1"], { resumes: [cached] });
  let finish!: (value: { resumes: typeof requested[] }) => void;
  vi.mocked(apiGet).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));

  render(tree(client));
  expect(screen.queryByRole("button", { name: /Download original/ })).not.toBeInTheDocument();
  await waitFor(() => expect(apiGet).toHaveBeenCalledOnce());
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByText("Original DOCX is ready")).not.toBeInTheDocument();
  expect(screen.queryByTitle(/Original resume:/)).not.toBeInTheDocument();
  expect(fetchResumeSource).not.toHaveBeenCalled();
  expect(fetchDirectResumeSource).not.toHaveBeenCalled();
  expect(apiBlob).not.toHaveBeenCalled();
  expect(apiDownload).not.toHaveBeenCalled();

  await act(async () => { finish({ resumes: [cached, requested] }); });
  fireEvent.click(await screen.findByRole("button", { name: "Download original TEX" }));
  await waitFor(() => expect(apiDownload).toHaveBeenCalledWith("/resume/31/source", "requested.tex"));
});

it("defaults to the first resume only when no resume query was supplied", async () => {
  query = "";
  await page();
  expect(screen.getByRole("combobox")).toHaveValue("7");
  fireEvent.click(screen.getByRole("button", { name: "Download original DOCX" }));
  await waitFor(() => expect(fetchDirectResumeSource).toHaveBeenCalledWith(7, expect.any(AbortSignal), "docx"));
});

it.each(["", "invalid", "0", "-1", "1.5", "9007199254740992"])("does not substitute another source for malformed resume=%s", async (value) => {
  query = `resume=${value}`;
  render(tree());
  await screen.findByText("Requested resume is unavailable");
  expect(screen.queryByRole("button", { name: /Download original/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(fetchResumeSource).not.toHaveBeenCalled();
  expect(fetchDirectResumeSource).not.toHaveBeenCalled();
  expect(apiBlob).not.toHaveBeenCalled();
  expect(apiDownload).not.toHaveBeenCalled();
});

it("keeps an unavailable explicit resume unselected and lets the owner refresh that exact request", async () => {
  query = "resume=31";
  render(tree());
  await screen.findByText("Requested resume is unavailable");
  expect(screen.queryByRole("button", { name: /Download original/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  vi.mocked(apiGet).mockResolvedValue({ resumes: [{ id: 31, filename: "requested.tex", source_available: true, source_format: "tex" }] });
  fireEvent.click(screen.getByRole("button", { name: "Retry loading" }));
  await screen.findByRole("button", { name: "Download original TEX" });
  expect(apiDownload).not.toHaveBeenCalled();
  expect(fetchResumeSource).not.toHaveBeenCalled();
});

it("does not carry a manual selection into a different resume URL", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rendered = render(tree(client));
  await screen.findByRole("button", { name: "Download original DOCX" });
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "8" } });
  expect(screen.getByRole("combobox")).toHaveValue("8");
  query = "resume=7&view=source";
  rendered.rerender(tree(client));
  // Unrelated query parameters do not change the selected document.
  expect(screen.getByRole("combobox")).toHaveValue("8");
  query = "resume=8";
  rendered.rerender(tree(client));
  query = "resume=7";
  rendered.rerender(tree(client));
  expect(screen.getByRole("combobox")).toHaveValue("7");
});

it("does not carry a manual selection into another owner's preview", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rendered = render(tree(client));
  await screen.findByRole("button", { name: "Download original DOCX" });
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "8" } });
  owner = "2";
  rendered.rerender(tree(client));
  await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("7"));
  expect(fetchDirectResumeSource).not.toHaveBeenCalled();
});

it("aborts the previous PDF when the requested resume changes and ignores its late bytes", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.mocked(apiGet).mockResolvedValue({ resumes: [
    { id: 7, filename: "first.pdf", source_available: true, source_format: "pdf" },
    { id: 8, filename: "second.pdf", source_available: true, source_format: "pdf" },
  ] });
  const pending = new Map<number, (blob: Blob) => void>();
  vi.mocked(fetchResumeSource).mockImplementation((id) => new Promise((resolve) => { pending.set(id, resolve); }));
  const create = vi.fn(() => "blob:http://localhost/second");
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
  const rendered = render(tree(client));
  await waitFor(() => expect(fetchResumeSource).toHaveBeenCalledWith(7, expect.any(AbortSignal), "pdf"));
  const firstSignal = vi.mocked(fetchResumeSource).mock.calls[0][1]!;
  query = "resume=8";
  rendered.rerender(tree(client));
  await waitFor(() => expect(fetchResumeSource).toHaveBeenCalledWith(8, expect.any(AbortSignal), "pdf"));
  expect(firstSignal.aborted).toBe(true);
  await act(async () => { pending.get(7)!(new Blob(["late-first"], { type: "application/pdf" })); });
  expect(create).not.toHaveBeenCalled();
  const second = new Blob(["second"], { type: "application/pdf" });
  await act(async () => { pending.get(8)!(second); });
  expect(create).toHaveBeenCalledExactlyOnceWith(second);
  expect(screen.getByTitle("Original resume: second.pdf")).toHaveAttribute("src", "blob:http://localhost/second");
});

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
