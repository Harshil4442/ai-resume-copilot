import { ownedBootstrapFixture } from "../../lib/fixtures/ownedBootstrap";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { trackEvent } from "../../lib/analytics";
import * as uploads from "../../lib/resumeUpload";
import type { ResumeParseResponse } from "../../lib/types";
import ResumePage from "./page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../../lib/api", () => ({ apiGet: api.get, apiPostJson: api.post, apiPatchJson: vi.fn(), apiPostForm: vi.fn(),
  ApiError: class extends Error { constructor(message: string, public status: number, public detail: unknown) { super(message); } } }));
const resume: ResumeParseResponse = { resume_id: 42, skills: ["Python"], experience_years: 1, sections: {}, contact_info: {}, source_available: true, source_format: "pdf", extraction_mode: "deterministic", enrichment_state: "not_requested", enrichment_units: 0, warnings: [] };
const clients: QueryClient[]=[];

beforeEach(()=>{vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED","true");vi.clearAllMocks();api.get.mockResolvedValue(ownedBootstrapFixture());});
afterEach(()=>{cleanup();clients.forEach((client)=>client.clear());clients.length=0;vi.restoreAllMocks();vi.unstubAllEnvs();});
function display(){const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});clients.push(client);return render(<QueryClientProvider client={client}><ResumePage/></QueryClientProvider>);}
function select(){fireEvent.change(screen.getByLabelText("Choose your source resume"),{target:{files:[new File(["synthetic file"],"resume.pdf",{type:"application/pdf"})]}});}

it("shows safety-check progress, offers cancellation and only publishes a saved resume",async()=>{
  let finish!: (result:ResumeParseResponse)=>void;
  vi.spyOn(uploads,"uploadResumeFile").mockImplementation((_file,options)=>{options?.onProgress?.({phase:"inspecting",mode:"direct"});return new Promise((resolve)=>{finish=resolve;});});
  display();select();fireEvent.click(screen.getByRole("button",{name:/parse resume/i}));
  expect(await screen.findByRole("status")).toHaveTextContent("Checking and extracting your resume");
  expect(screen.getByRole("button",{name:"Cancel upload"})).toBeEnabled();
  expect(screen.queryByText("Resume parsed")).toBeNull();
  await act(async()=>{finish(resume);});
  expect(await screen.findByText("Resume parsed")).toBeVisible();
  expect(screen.getByRole("link",{name:/view original resume/i})).toHaveAttribute("href","/resume/preview?resume=42");
  await waitFor(()=>expect(trackEvent).toHaveBeenCalledWith("resume_upload_completed",{resume_id:42,skill_count:1}));
});

it("aborts cancellation and ignores a late result that does not honor its signal",async()=>{
  let signal:AbortSignal|undefined;let finish!:(result:ResumeParseResponse)=>void;
  vi.spyOn(uploads,"uploadResumeFile").mockImplementation((_file,options)=>{signal=options?.signal;options?.onProgress?.({phase:"queued",mode:"direct"});return new Promise((resolve)=>{finish=resolve;});});
  display();select();fireEvent.click(screen.getByRole("button",{name:/parse resume/i}));
  fireEvent.click(await screen.findByRole("button",{name:"Cancel upload"}));
  expect(signal?.aborted).toBe(true);
  await act(async()=>{finish(resume);});
  expect(screen.queryByText("Resume parsed")).toBeNull();
  expect(trackEvent).not.toHaveBeenCalledWith("resume_upload_completed",expect.anything());
  expect(screen.getByRole("button",{name:/parse resume/i})).toBeEnabled();
});

it("unmounting aborts upload work and prevents a later saved result changing the page",async()=>{
  let signal:AbortSignal|undefined;let finish!:(result:ResumeParseResponse)=>void;
  vi.spyOn(uploads,"uploadResumeFile").mockImplementation((_file,options)=>{signal=options?.signal;return new Promise((resolve)=>{finish=resolve;});});
  const view=display();select();fireEvent.click(screen.getByRole("button",{name:/parse resume/i}));view.unmount();
  expect(signal?.aborted).toBe(true);await act(async()=>{finish(resume);});
  expect(trackEvent).not.toHaveBeenCalledWith("resume_upload_completed",expect.anything());
});

it("refuses the large disabled-route file before starting an upload",async()=>{
  vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED","false");
  const send=vi.spyOn(uploads,"uploadResumeFile");display();
  fireEvent.change(screen.getByLabelText("Choose your source resume"),{target:{files:[new File([new Uint8Array(5*1024*1024)],"resume.pdf",{type:"application/pdf"})]}});
  expect(await screen.findByRole("alert")).toHaveTextContent("cannot pass through the current website upload route");
  expect(send).not.toHaveBeenCalled();expect(screen.getByRole("button",{name:/parse resume/i})).toBeDisabled();
});


it("uploads without AI first and exposes a separate enrichment review for unified credits", async () => {
  api.get.mockResolvedValue(ownedBootstrapFixture({ availability: { catalog_enabled: true, execution_enabled: true, supported_operations: ["resume_enrichment"],
    free_core_operations: ["resume_parse"], free_core_limits: { resume_parse: { per_account_30_days: 1, max_input_bytes: 5000000, max_source_records: 1 } } } }));
  const upload = vi.spyOn(uploads, "uploadResumeFile").mockResolvedValue(resume);
  display(); select(); await screen.findByText(/After the source is saved/);
  expect(screen.queryByRole("checkbox")).toBeNull(); fireEvent.click(screen.getByRole("button", { name: /Parse resume/ }));
  await screen.findByRole("button", { name: "Review skill enrichment charge" });
  expect(upload).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({ enrichSkills: false }));
  expect(api.post).not.toHaveBeenCalled();
  expect(screen.getByText(/1 new parsed resume per rolling 30 days/)).toBeVisible();
});
