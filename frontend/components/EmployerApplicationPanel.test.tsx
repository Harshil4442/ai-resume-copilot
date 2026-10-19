import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { apiGet, apiPostForm, apiPostJson, apiPutJson } from "../lib/api";
import * as uploads from "../lib/resumeUpload";
import type { EmployerApplication, JobServiceCatalog } from "../lib/employerJobs";
import { accountMutationHeaders } from "../lib/accountTransportClient";
import EmployerApplicationPanel from "./EmployerApplicationPanel";

vi.mock("../lib/api", () => ({
  apiGet: vi.fn(), apiPostForm: vi.fn(), apiPostJson: vi.fn(), apiPutJson: vi.fn(),
  apiBlob: vi.fn(), apiDownload: vi.fn(),
}));
vi.mock("../lib/accountTransportClient", () => ({ accountMutationHeaders: vi.fn() }));

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
    search_credits_per_job: 2, apply_credits_per_job: 7, pricing_version: "fixture-price", max_search_jobs: 20, balance: 10, sources: [] };
  vi.mocked(apiGet).mockImplementation(async (path) => path.endsWith("/catalog") ? catalog : application());
  vi.mocked(apiPostForm).mockResolvedValue({ resume_id: 42, source_available: true, source_format: "tex" });
  vi.mocked(accountMutationHeaders).mockResolvedValue({ "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "9".repeat(64) });
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; vi.unstubAllGlobals(); });

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

it("offers the exact Razorpay companion handoff and application ID without approving, applying or charging", async () => {
  const saved = application();
  saved.resume_choice = "original";
  saved.artifact = { filename: "Synthetic resume.docx", media_type: docxType, size_bytes: 1024, sha256: "a".repeat(64), preview_url: "/v1/employer-jobs/applications/app_local/artifact" };
  saved.posting.employer = "Razorpay Software Private Limited";
  saved.posting.canonical_url = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005";
  saved.posting.apply_url = saved.posting.canonical_url;
  vi.mocked(apiGet).mockResolvedValue(saved);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } }); clients.push(client);
  render(<QueryClientProvider client={client}><EmployerApplicationPanel id="app_local" onClose={vi.fn()} onChange={vi.fn()} resumes={[]} /></QueryClientProvider>);
  expect(await screen.findByRole("link", { name: "Review browser assistance" })).toHaveAttribute("href", "/browser-companion?application=app_local");
  expect(screen.getByRole("link", { name: "Open Razorpay application" })).toHaveAttribute("href", saved.posting.apply_url);
  expect(screen.getByRole("button", { name: "Copy application ID" })).toBeEnabled();
  expect(screen.getByText("app_local")).toBeVisible();
  expect(apiPostJson).not.toHaveBeenCalled(); expect(apiPutJson).not.toHaveBeenCalled();
});

describe("tailoring workspace follows the chosen resume for this job", () => {
  async function openTailoring() {
    const saved = manualApplication();
    vi.mocked(apiGet).mockImplementation(async (path) => path.startsWith("/v1/resume-versions?") ? [] : path.endsWith("/catalog") ? {
      enabled: true, auto_submit_enabled: false, search_credits_per_job: 1, apply_credits_per_job: 20,
      pricing_version: "synthetic", max_search_jobs: 20, balance: 0, sources: [],
    } : saved);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } }); clients.push(client);
    const resumes = [1, 2].map((id) => ({ id, filename: `synthetic-${id}.docx`, source_available: true, source_format: "docx" as const, created_at: "2026-10-16T00:00:00Z" }));
    render(<QueryClientProvider client={client}><EmployerApplicationPanel id="app_local" onClose={vi.fn()} onChange={vi.fn()} resumes={resumes} /></QueryClientProvider>);
    await screen.findByRole("heading", { name: "Engineer" });
    await userEvent.selectOptions(screen.getByLabelText("Resume choice"), "tailored");
  }

  it("requires a new explicit preparation after switching source resumes", async () => {
    vi.mocked(apiPostJson).mockResolvedValueOnce({ id: "tailoring_resume_one" }).mockResolvedValueOnce({ id: "tailoring_resume_two" });
    await openTailoring();
    await userEvent.click(screen.getByRole("button", { name: "Prepare tailored resume for this job" }));
    expect(await screen.findByRole("link", { name: "Review tailoring for this job in Workspace" })).toHaveAttribute("href", "/workspace/tailoring_resume_one?tab=resume");
    await userEvent.selectOptions(screen.getByLabelText("Saved original file"), "2");
    expect(screen.queryByRole("link", { name: "Review tailoring for this job in Workspace" })).toBeNull();
    expect(apiPostJson).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Prepare tailored resume for this job" }));
    expect(await screen.findByRole("link", { name: "Review tailoring for this job in Workspace" })).toHaveAttribute("href", "/workspace/tailoring_resume_two?tab=resume");
    expect(vi.mocked(apiPostJson).mock.calls.map(([path, payload]) => [path, (payload as { resume_id: number }).resume_id])).toEqual([["/v1/opportunities", 1], ["/v1/opportunities", 2]]);
    expect(apiPutJson).not.toHaveBeenCalled();
  });

  it("does not expose a late first-resume result for the newly chosen resume", async () => {
    const reply = Promise.withResolvers<unknown>();
    vi.mocked(apiPostJson).mockReturnValue(reply.promise);
    await openTailoring();
    await userEvent.click(screen.getByRole("button", { name: "Prepare tailored resume for this job" }));
    await userEvent.selectOptions(screen.getByLabelText("Saved original file"), "2");
    await act(async () => { reply.resolve({ id: "tailoring_resume_one" }); });
    expect(screen.queryByRole("link", { name: "Review tailoring for this job in Workspace" })).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Prepare tailored resume for this job" })).toBeEnabled());
    expect(screen.queryByRole("link", { name: "Review tailoring for this job in Workspace" })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Saved original file"), "1");
    expect(screen.getByRole("link", { name: "Review tailoring for this job in Workspace" })).toHaveAttribute("href", "/workspace/tailoring_resume_one?tab=resume");
    expect(apiPostJson).toHaveBeenCalledTimes(1); expect(apiPutJson).not.toHaveBeenCalled();
  });
});

function manualApplication(): EmployerApplication {
  const saved = application(); saved.status = "manual_handoff"; saved.resume_choice = "original"; saved.credit_cost = 0; saved.reserved_credits = 0; saved.receipt = null;
  saved.artifact = { filename: "Synthetic retained resume.docx", media_type: docxType, size_bytes: 1024, sha256: "a".repeat(64), preview_url: "/v1/employer-jobs/applications/app_local/artifact" };
  saved.posting.apply_url = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4739199005"; saved.posting.canonical_url = saved.posting.apply_url;
  saved.form.fields = [{ id: "first_name", label: "First Name", type: "text", required: true, options: [] }]; saved.answers = { first_name: "Synthetic Alice" };
  return saved;
}
function reportProjection() {
  return { id: "app_local", status: "user_reported", application_mode: "manual", charged_credits: 0, reserved_credits: 0, receipt: null,
    candidate_report: { source: "candidate_report", reported_at: "2026-10-14T18:00:00+00:00", saved_package_digest: "a".repeat(64), employer_confirmed: false, used_file_verified: false } };
}
async function renderManual(read: () => EmployerApplication = manualApplication) {
  const catalog: JobServiceCatalog = { enabled: true, auto_submit_enabled: false, search_credits_per_job: 1, apply_credits_per_job: 20, pricing_version: "synthetic", max_search_jobs: 20, balance: 0, sources: [] };
  vi.mocked(apiGet).mockImplementation(async (path) => path.endsWith("/catalog") ? catalog : read());
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } }); clients.push(client);
  render(<QueryClientProvider client={client}><EmployerApplicationPanel id="app_local" onClose={vi.fn()} onChange={vi.fn()} resumes={[]} /></QueryClientProvider>);
  await screen.findByRole("heading", { name: "Engineer" });
}
async function recordManualReport() {
  await userEvent.click(screen.getByRole("checkbox", { name: /I reviewed this saved package and report/ }));
  await userEvent.click(screen.getByRole("button", { name: "Record my manual submission" }));
}

describe("truthful candidate-reported manual application outcome", () => {
  it("requires explicit review before its exact CSRF-bound report, retains the timestamp and stops duplicate filling/reporting", async () => {
    const send = vi.fn().mockResolvedValue(Response.json(reportProjection())); vi.stubGlobal("fetch", send); await renderManual();
    expect(screen.getByRole("button", { name: "Record my manual submission" })).toBeDisabled(); expect(send).not.toHaveBeenCalled();
    await recordManualReport(); expect(await screen.findByRole("heading", { name: "Submission reported by you" })).toBeVisible();
    expect(send).toHaveBeenCalledTimes(1); expect(accountMutationHeaders).toHaveBeenCalledTimes(1);
    const [url, init] = send.mock.calls[0]; expect(url).toBe("/api/employer-applications/app_local/report-submitted");
    expect(JSON.parse(init.body)).toEqual({ package_digest: "a".repeat(64), confirmed: true }); expect(init.headers["X-Hirewiz-Account-CSRF"]).toBe("9".repeat(64)); expect(init.redirect).toBe("error");
    expect(screen.getByText(/Employer confirmation and the file actually used are unverified/)).toBeVisible();
    expect(screen.getByText(`Report recorded: ${new Date("2026-10-14T18:00:00+00:00").toLocaleString("en-IN")}`)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Employer submission confirmed" })).toBeNull(); expect(screen.queryByRole("button", { name: "Record my manual submission" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Review browser assistance" })).toBeNull(); expect(screen.getByLabelText("Resume choice")).toBeDisabled(); expect(screen.getByLabelText("First Name (required)")).toBeDisabled();
    expect(apiPostJson).not.toHaveBeenCalled(); expect(apiPutJson).not.toHaveBeenCalled();
  });

  it("loads a previously retained user report with an immutable form and no provider-confirmed heading", async () => {
    const saved = { ...manualApplication(), ...reportProjection() } as EmployerApplication;
    const send = vi.fn(); vi.stubGlobal("fetch", send); await renderManual(() => saved);
    expect(screen.getByRole("heading", { name: "Submission reported by you" })).toBeVisible(); expect(screen.getByLabelText("Resume choice")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Record my manual submission" })).toBeNull(); expect(screen.queryByRole("link", { name: "Review browser assistance" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Employer submission confirmed" })).toBeNull(); expect(send).not.toHaveBeenCalled();
  });

  it("freezes report/edit/fill after a lost reply and reconciles a persisted report through an explicit status refresh", async () => {
    let current = manualApplication(); const send = vi.fn().mockRejectedValue(new Error("Synthetic reply lost")); vi.stubGlobal("fetch", send); await renderManual(() => current);
    await recordManualReport(); expect(await screen.findByText(/The outcome needs a status check/)).toBeVisible();
    expect(send).toHaveBeenCalledTimes(1); expect(screen.getByRole("button", { name: "Record my manual submission" })).toBeDisabled();
    expect(screen.getByLabelText("Resume choice")).toBeDisabled(); expect(screen.getByLabelText("First Name (required)")).toBeDisabled(); expect(screen.getByRole("button", { name: "Cancel remaining work" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Review browser assistance" })).toBeNull();
    current = { ...current, ...reportProjection() } as EmployerApplication;
    await userEvent.click(screen.getByRole("button", { name: "Refresh application status" })); expect(await screen.findByRole("heading", { name: "Submission reported by you" })).toBeVisible();
    expect(send).toHaveBeenCalledTimes(1); expect(screen.queryByRole("button", { name: "Record my manual submission" })).toBeNull();
  });

  it("allows a new explicit review only after a successful status refresh proves the report was not recorded", async () => {
    const send = vi.fn().mockRejectedValue(new Error("Synthetic reply lost")); vi.stubGlobal("fetch", send); await renderManual();
    await recordManualReport(); await screen.findByText(/The outcome needs a status check/);
    expect(screen.getByRole("checkbox", { name: /I reviewed this saved package and report/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Refresh application status" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /I reviewed this saved package and report/ })).toBeEnabled());
    expect(screen.getByRole("checkbox", { name: /I reviewed this saved package and report/ })).not.toBeChecked(); expect(screen.getByRole("button", { name: "Record my manual submission" })).toBeDisabled(); expect(send).toHaveBeenCalledTimes(1);
  });

  it.each(["provider-confirmed", "charged", "wrong-digest"])("rejects a misleading %s report reply instead of claiming a confirmed or recorded submission", async (kind) => {
    const reply = reportProjection(); if (kind === "provider-confirmed") reply.status = "confirmed"; if (kind === "charged") reply.charged_credits = 1; if (kind === "wrong-digest") reply.candidate_report.saved_package_digest = "b".repeat(64);
    const send = vi.fn().mockResolvedValue(Response.json(reply)); vi.stubGlobal("fetch", send); await renderManual(); await recordManualReport();
    expect(await screen.findByText(/The outcome needs a status check/)).toBeVisible(); expect(screen.queryByRole("heading", { name: "Submission reported by you" })).toBeNull(); expect(screen.queryByRole("heading", { name: "Employer submission confirmed" })).toBeNull();
    expect(screen.getByRole("button", { name: "Record my manual submission" })).toBeDisabled(); expect(screen.getByLabelText("Resume choice")).toBeDisabled(); expect(send).toHaveBeenCalledTimes(1);
  });
});
