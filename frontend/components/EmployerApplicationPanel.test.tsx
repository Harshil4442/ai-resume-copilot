import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiGet, apiPostForm, apiPostJson, apiPutJson } from "../lib/api";
import * as uploads from "../lib/resumeUpload";
import type { EmployerApplication, JobServiceCatalog } from "../lib/employerJobs";
import EmployerApplicationPanel from "./EmployerApplicationPanel";

vi.mock("../lib/api", () => ({
  apiGet: vi.fn(), apiPostForm: vi.fn(), apiPostJson: vi.fn(), apiPutJson: vi.fn(),
  apiBlob: vi.fn(), apiDownload: vi.fn(),
}));

const docxType = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const maximum = 5 * 1024 * 1024;
const texSource = "\\documentclass{article}\\usepackage[T1]{fontenc}\\usepackage{lmodern}\\begin{document}\nBuilt Python reports.\n\\end{document}";
const sourceZipBase64 = "UEsDBBQAAAAIANu5SV2VnuAKYgAAAHgAAAAKAAAAcmVzdW1lLnRleItJyU8uzU3NK0nOSSwurk4sKslMzkmtjSktTi1ITM5OTE+NDjGMrU7LzytJzUtGFq/Oyc1PSS3Kq41JSk3PzKuGGVTL5VSamVOiEFBZkpGfp1CUWpBfVFKsxxWTmpeCUAQAUEsBAhQDFAAAAAgA27lJXZWe4ApiAAAAeAAAAAoAAAAAAAAAAAAAAIABAAAAAHJlc3VtZS50ZXhQSwUGAAAAAAEAAQA4AAAAigAAAAAA";
const clients: QueryClient[] = [];

function application(): EmployerApplication {
  return {
    id: "app_local", status: "needs_action", application_mode: "manual", resume_id: 1,
    resume_choice: "custom", resume_version_id: null, package_digest: "a".repeat(64),
    artifact: null, answers: {}, consents: {}, missing_fields: [], credit_cost: 7,
    charged_credits: 0, requires_user_action: true, error: null,
    form: { version: "local", fields: [], consents: [] },
    posting: { id: "job_local", source_id: "src_local", external_id: "local", title: "Engineer",
      employer: "Local fixture", location: "Remote", description: "Local upload review fixture",
      canonical_url: "https://employer.example/careers/local", apply_url: "https://employer.example/apply/local",
      platform: "greenhouse", publication_at: null, last_checked_at: "2026-10-09T00:00:00Z", application_mode: "manual" },
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  const catalog: JobServiceCatalog = { enabled: true, auto_submit_enabled: false,
    search_credits_per_job: 2, apply_credits_per_job: 7, max_search_jobs: 20, balance: 10, sources: [] };
  vi.mocked(apiGet).mockImplementation(async (path) => path.endsWith("/catalog") ? catalog : application());
  vi.mocked(apiPostForm).mockResolvedValue({ resume_id: 42, source_available: true, source_format: "tex" });
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

async function upload(file: File) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><EmployerApplicationPanel id="app_local" onClose={vi.fn()} onChange={vi.fn()} resumes={[]} /></QueryClientProvider>);
  const input = await screen.findByLabelText("Upload a custom PDF, DOCX or TeX source");
  await act(async () => { fireEvent.change(input, { target: { files: [file] } }); });
}

describe("custom resume upload admission through the application review UI", () => {
  it.each([
    ["resume.pdf", "application/pdf"], ["resume.docx", docxType],
    ["resume.tex", "application/x-tex"], ["resume.TEX", "text/x-tex"],
    ["resume.tex", "text/plain"], ["source.zip", "application/zip"],
    ["source.ZIP", "application/x-zip-compressed"], ["source.zip", "application/octet-stream"],
    ["resume.tex", ""], ["resume.pdf", ""],
  ])("sends supported %s (%s) unchanged for server validation, without applying or approving", async (name, type) => {
    const contents = /\.zip$/i.test(name) ? Uint8Array.from(atob(sourceZipBase64), (character) => character.charCodeAt(0)) : /\.tex$/i.test(name) ? texSource : "Local source upload fixture";
    const file = new File([contents], name, { type });
    await upload(file);
    await waitFor(() => expect(apiPostForm).toHaveBeenCalledTimes(1));
    const [path, body] = vi.mocked(apiPostForm).mock.calls[0];
    expect(path).toBe("/resume/parse");
    expect(body.get("file")).toBe(file);
    expect(apiPostJson).not.toHaveBeenCalled();
    expect(apiPutJson).not.toHaveBeenCalled();
    await screen.findByText("Your saved preview is out of date. Save the package to preview the new file.");
  });

  it.each([
    ["script.exe", "application/octet-stream", 20], ["resume.txt", "text/plain", 20],
    ["photo.png", "application/pdf", 20], ["resume.pdf", "application/x-msdownload", 20],
    ["resume.tex", "image/png", 20], ["resume.zip", "application/zip", maximum + 1],
    ["resume.pdf", "application/pdf", maximum + 1],
  ])("rejects unsupported or oversized %s (%s; %i bytes) before a request", async (name, type, size) => {
    await upload(new File([new Uint8Array(size)], name, { type }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no larger than 5 MB/);
    expect(apiPostForm).not.toHaveBeenCalled();
    expect(apiPostJson).not.toHaveBeenCalled();
    expect(apiPutJson).not.toHaveBeenCalled();
  });

  it("does not send a 5 MB native source through the limited BFF route", async () => {
    await upload(new File([new Uint8Array(maximum)], "source.zip", { type: "application/zip" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot pass through the current website upload route");
    expect(apiPostForm).not.toHaveBeenCalled();
  });

  it("shows the server's source refusal without approving or changing the saved package", async () => {
    vi.mocked(apiPostForm).mockRejectedValue(new Error("This TeX project is unsupported. Upload your original/custom PDF."));
    await upload(new File(["unsupported source fixture"], "resume.tex", { type: "text/plain" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This TeX project is unsupported. Upload your original/custom PDF.");
    expect(apiPostForm).toHaveBeenCalledTimes(1);
    expect(apiPutJson).not.toHaveBeenCalled();
    expect(apiPostJson).not.toHaveBeenCalled();
    expect(screen.queryByText("Your saved preview is out of date. Save the package to preview the new file.")).toBeNull();
  });
});


it("custom-upload cancellation never replaces the saved application with a late result", async () => {
  let signal: AbortSignal | undefined;
  let finish!: (result: import("../lib/types").ResumeParseResponse) => void;
  const send = vi.spyOn(uploads, "uploadResumeFile").mockImplementation((_file, options) => {
    signal = options?.signal; options?.onProgress?.({ phase: "inspecting", mode: "direct" });
    return new Promise((resolve) => { finish = resolve; });
  });
  try {
    await upload(new File(["synthetic source"], "resume.pdf", { type: "application/pdf" }));
    expect(await screen.findByRole("button", { name: "Cancel upload" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel upload" }));
    expect(signal?.aborted).toBe(true);
    await act(async () => { finish({ resume_id: 42, skills: [], experience_years: 0, sections: {}, contact_info: {}, source_available: true, source_format: "pdf" }); });
    expect(screen.queryByText("Your saved preview is out of date. Save the package to preview the new file.")).toBeNull();
    expect(apiPutJson).not.toHaveBeenCalled(); expect(apiPostJson).not.toHaveBeenCalled();
  } finally { send.mockRestore(); }
});
