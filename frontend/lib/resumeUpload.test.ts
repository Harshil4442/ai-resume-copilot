import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiPostForm } from "./api";
import { MAX_LEGACY_RESUME_BYTES, MAX_RESUME_BYTES, ResumeUploadCancelled, uploadResumeFile } from "./resumeUpload";

vi.mock("./api", () => ({ apiPostForm: vi.fn() }));
const id = "rup_" + "a".repeat(32);
const resume = { resume_id: 42, source_available: true, source_format: "pdf", skills: ["Python"], experience_years: 1, sections: {}, contact_info: {} };
const fetchMock = vi.fn<typeof fetch>();

function file(size = 20, name = "resume.pdf", type = "application/pdf") {
  const bytes = new Uint8Array(size);
  bytes.fill(7);
  const value = new File([bytes], name, { type });
  Object.defineProperty(value, "arrayBuffer", { value: async () => bytes.buffer });
  return value;
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function status(state = "released") { return { upload_id: id, state, error_code: null, resume: state === "released" ? resume : null }; }
function intent(value: File) {
  const query = new URLSearchParams({ "X-Goog-Algorithm": "GOOG4-RSA-SHA256", "X-Goog-Credential": "synthetic-signer", "X-Goog-Date": "20261010T000000Z",
    "X-Goog-Expires": "300", "X-Goog-SignedHeaders": "content-type;host;x-goog-content-length-range;x-goog-if-generation-match", "X-Goog-Signature": "a".repeat(512) });
  return { upload_id: id, state: "awaiting_upload", upload_url: `https://storage.googleapis.com/synthetic-quarantine/quarantine/${id}/source?${query}`,
    method: "PUT", headers: { "Content-Type": /\.pdf$/i.test(value.name) ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "x-goog-if-generation-match": "0", "x-goog-content-length-range": `${value.size},${value.size}` },
    upload_grant_expires_at: new Date(Date.now()+300_000).toISOString(), expires_at: new Date(Date.now()+900_000).toISOString() };
}
function serve(value: File, overrides: { instruction?: ReturnType<typeof intent>; put?: () => Promise<Response>; complete?: () => Promise<Response>; get?: () => Promise<Response>; cancel?: () => Promise<Response> } = {}) {
  fetchMock.mockImplementation(async (url, init) => {
    if (String(url).startsWith("https://storage.googleapis.com/")) return overrides.put ? overrides.put() : new Response(null, { status: 200 });
    if (init?.method === "DELETE") return overrides.cancel ? overrides.cancel() : json(status("cancelled"));
    if (String(url).endsWith("/complete")) return overrides.complete ? overrides.complete() : json(status());
    if (init?.method === "GET") return overrides.get ? overrides.get() : json(status());
    return json(overrides.instruction ?? intent(value));
  });
}
const puts = () => fetchMock.mock.calls.filter(([url]) => String(url).startsWith("https://storage.googleapis.com/"));
const deletes = () => fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");

beforeEach(() => {
  vi.resetAllMocks(); vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "true");
  vi.stubGlobal("crypto", webcrypto); vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("direct private resume upload contract", () => {
  it("hashes exact 5 MiB, sends original File once, then returns only the saved verdict", async () => {
    const value = file(MAX_RESUME_BYTES); serve(value);
    const store = vi.spyOn(Storage.prototype, "setItem");
    const log = vi.spyOn(console, "log");
    expect(await uploadResumeFile(value)).toEqual(resume);
    const create = fetchMock.mock.calls[0][1]!;
    const body = JSON.parse(create.body as string);
    expect(body.size_bytes).toBe(MAX_RESUME_BYTES);
    expect(body.sha256).toBe(createHash("sha256").update(new Uint8Array(MAX_RESUME_BYTES).fill(7)).digest("hex"));
    expect(create.credentials).toBe("same-origin");
    expect((create.headers as Record<string,string>)["Idempotency-Key"]).toMatch(/^[a-f0-9-]{36}$/);
    expect(puts()).toHaveLength(1);
    expect(puts()[0][1]).toMatchObject({ method: "PUT", body: value, credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store" });
    expect((puts()[0][1]?.headers as Record<string,string>)["x-goog-content-length-range"]).toBe(`${MAX_RESUME_BYTES},${MAX_RESUME_BYTES}`);
    expect(apiPostForm).not.toHaveBeenCalled(); expect(store).not.toHaveBeenCalled(); expect(log).not.toHaveBeenCalled();
    store.mockRestore(); log.mockRestore();
  });

  it("reconciles a lost PUT reply without re-uploading or using the legacy route", async () => {
    const value = file(); serve(value, { put: async () => { throw new TypeError("synthetic lost reply"); } });
    const phases: string[] = [];
    expect(await uploadResumeFile(value, { onProgress: (p) => phases.push(p.phase) })).toEqual(resume);
    expect(phases).toContain("reconciling"); expect(puts()).toHaveLength(1); expect(apiPostForm).not.toHaveBeenCalled();
  });

  it("polls queued and inspecting states until a bound released response", async () => {
    vi.useFakeTimers(); const value=file(); let checks=0;
    serve(value, { complete: async () => json(status("queued")), get: async () => json(status(++checks === 1 ? "inspecting" : "released")) });
    let waiting!: () => void; const queued = new Promise<void>((resolve) => { waiting=resolve; });
    const phases: string[]=[];
    const result=uploadResumeFile(value, { onProgress: (p) => { phases.push(p.phase); if(p.phase==="queued") waiting(); } });
    await queued; await vi.advanceTimersByTimeAsync(4000);
    expect(await result).toEqual(resume); expect(phases).toContain("inspecting"); expect(checks).toBe(2); expect(puts()).toHaveLength(1);
  });

  it("resolves unknown completion from status and retries only idempotent completion", async () => {
    vi.useFakeTimers(); const value=file(); let completed=0;
    serve(value, { complete: async () => { if(++completed===1) throw new TypeError("synthetic completion reply lost"); return json(status()); }, get: async () => json(status("awaiting_upload")) });
    let waiting!: () => void; const queued=new Promise<void>((resolve)=>{waiting=resolve;});
    const result=uploadResumeFile(value, { onProgress:(p)=>{if(p.phase==="queued") waiting();} });
    await queued; await vi.advanceTimersByTimeAsync(2000);
    expect(await result).toEqual(resume); expect(completed).toBe(2); expect(puts()).toHaveLength(1);
  });

  it.each(["rejected", "failed", "expired", "cancelled"])("does not publish a %s verdict or charge/approve anything", async (state) => {
    const value=file(); serve(value,{complete:async()=>json(status(state))});
    await expect(uploadResumeFile(value)).rejects.toThrow(/resume|document/i);
    expect(puts()).toHaveLength(1); expect(deletes()).toHaveLength(1); expect(apiPostForm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([url]) => /approve|execute|billing|enrich/.test(String(url)))).toBe(false);
  });

  it("fails immediately for an unbound status instead of waiting or retrying PUT", async () => {
    const value=file(); serve(value,{complete:async()=>json({...status(),upload_id:"rup_"+"b".repeat(32)})});
    await expect(uploadResumeFile(value)).rejects.toThrow("Resume status could not be verified");
    expect(puts()).toHaveLength(1); expect(deletes()).toHaveLength(1);
  });

  it("cancellation during PUT requests cleanup and never calls completion", async () => {
    const value=file(); const controller=new AbortController(); let started!:()=>void;
    const uploading=new Promise<void>((resolve)=>{started=resolve;});
    serve(value);
    const standard=fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async(url,init)=>{
      if(String(url).startsWith("https://storage.googleapis.com/")) return new Promise<Response>((_resolve,reject)=>{
        started(); init?.signal?.addEventListener("abort",()=>reject(new DOMException("stopped","AbortError")),{once:true});
      });
      return standard(url,init);
    });
    const result=uploadResumeFile(value,{signal:controller.signal});
    const rejected=expect(result).rejects.toMatchObject({name:"ResumeUploadCancelled",cleanup:"requested"});
    await uploading; controller.abort(); await rejected;
    expect(deletes()).toHaveLength(1); expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith("/complete"))).toBe(false);
  });

  it("cancellation after a release conflict honestly reports the saved file", async () => {
    const value=file(); const controller=new AbortController(); serve(value,{cancel:async()=>json({detail:{code:"saved_resume_requires_deletion"}},409)});
    await expect(uploadResumeFile(value,{signal:controller.signal,onProgress:(p)=>{if(p.phase==="uploading")controller.abort();}})).rejects.toMatchObject({cleanup:"already_saved"});
    expect(puts()).toHaveLength(0); expect(deletes()).toHaveLength(1);
  });

  it("does not call an unrelated conflict a saved resume", async () => {
    const value = file(); const controller = new AbortController();
    serve(value, { cancel: async () => json({ detail: { code: "unrelated_conflict" } }, 409) });
    await expect(uploadResumeFile(value, { signal: controller.signal, onProgress: (p) => { if (p.phase === "uploading") controller.abort(); } })).rejects.toMatchObject({ cleanup: "unconfirmed" });
    expect(puts()).toHaveLength(0);
  });

  it.each([{}, { ...status("cancelled"), upload_id: "rup_" + "b".repeat(32) }, status("queued")])("requires this upload's cancelled acknowledgment before reporting requested cleanup", async (acknowledgment) => {
    const value = file(); const controller = new AbortController();
    serve(value, { cancel: async () => json(acknowledgment) });
    await expect(uploadResumeFile(value, { signal: controller.signal, onProgress: (p) => { if (p.phase === "uploading") controller.abort(); } })).rejects.toMatchObject({ cleanup: "unconfirmed" });
    expect(puts()).toHaveLength(0); expect(deletes()).toHaveLength(1);
  });

  it("refuses a released format that disagrees with the original file", async () => {
    const value = file(); serve(value, { complete: async () => json({ ...status(), resume: { ...resume, source_format: "docx" } }) });
    await expect(uploadResumeFile(value)).rejects.toThrow("Saved resume could not be verified");
    expect(puts()).toHaveLength(1); expect(deletes()).toHaveLength(1);
  });

  it("never displays content from a malformed JSON response", async () => {
    fetchMock.mockResolvedValue(new Response("synthetic private source content", { headers: { "Content-Type": "application/json" } }));
    await expect(uploadResumeFile(file())).rejects.toThrow("Resume upload returned an invalid response.");
    expect(puts()).toHaveLength(0);
  });

  it("stops polling at the absolute four-minute deadline and requests cleanup", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException("Deadline", "TimeoutError")), milliseconds);
      return controller.signal;
    });
    const value = file(); let checks = 0;
    serve(value, { complete: async () => json(status("queued")), get: async () => { checks++; return json(status("queued")); } });
    let waiting!: () => void; const queued = new Promise<void>((resolve) => { waiting = resolve; });
    const result = uploadResumeFile(value, { onProgress: (p) => { if (p.phase === "queued") waiting(); } });
    const rejected = expect(result).rejects.toThrow("Resume checks took too long");
    await queued; await vi.advanceTimersByTimeAsync(240_000); await rejected;
    expect(checks).toBeGreaterThan(0); expect(checks).toBeLessThanOrEqual(120);
    expect(puts()).toHaveLength(1); expect(deletes()).toHaveLength(1);
  });

  it("does not claim timeout cleanup when the cancellation request failed", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException("Deadline", "TimeoutError")), milliseconds);
      return controller.signal;
    });
    const value = file();
    serve(value, { complete: async () => json(status("queued")), get: async () => json(status("queued")), cancel: async () => json({}, 503) });
    let waiting!: () => void; const queued = new Promise<void>((resolve) => { waiting = resolve; });
    const result = uploadResumeFile(value, { onProgress: (p) => { if (p.phase === "queued") waiting(); } });
    const rejected = expect(result).rejects.toThrow("Cleanup could not be confirmed");
    await queued; await vi.advanceTimersByTimeAsync(240_000); await rejected;
    expect(deletes()).toHaveLength(1);
  });

  it("does not quietly fall back if the direct service is unavailable", async () => {
    fetchMock.mockResolvedValue(json({},503));
    await expect(uploadResumeFile(file())).rejects.toThrow("Secure resume upload is unavailable");
    expect(apiPostForm).not.toHaveBeenCalled(); expect(puts()).toHaveLength(0);
  });
});

describe("capability and size admission",()=>{
  it.each(["http", "host", "user", "port", "path", "mime", "range", "generation", "extra-header", "signed-header", "expiry"])("refuses an altered %s capability before file bytes leave",async(change)=>{
    const value=file(); const instructions=intent(value);
    if(change==="http")instructions.upload_url=instructions.upload_url.replace("https:","http:");
    if(change==="host")instructions.upload_url=instructions.upload_url.replace("storage.googleapis.com","synthetic-attacker.invalid");
    if(change==="user")instructions.upload_url=instructions.upload_url.replace("https://","https://user@");
    if(change==="port")instructions.upload_url=instructions.upload_url.replace("storage.googleapis.com/","storage.googleapis.com:444/");
    if(change==="path")instructions.upload_url=instructions.upload_url.replace("/source?","/other?");
    if(change==="mime")instructions.headers["Content-Type"]="text/plain";
    if(change==="range")instructions.headers["x-goog-content-length-range"]="0,9999";
    if(change==="generation")instructions.headers["x-goog-if-generation-match"]="1";
    if(change==="extra-header")(instructions.headers as Record<string,string>).Authorization="synthetic-secret";
    if(change==="signed-header")instructions.upload_url=instructions.upload_url.replace("content-type%3Bhost%3B", "authorization%3Bhost%3B");
    if(change==="expiry")instructions.upload_grant_expires_at=new Date(Date.now()-1000).toISOString();
    serve(value,{instruction:instructions});
    await expect(uploadResumeFile(value)).rejects.toThrow("Secure upload instructions are invalid");
    expect(puts()).toHaveLength(0); expect(deletes()).toHaveLength(1);
  });
  it.each([file(0),file(MAX_RESUME_BYTES+1),file(20,"bad.exe"),file(20,"resume.pdf","application/x-msdownload")])("rejects invalid file metadata before request or hashing",async(value)=>{
    await expect(uploadResumeFile(value)).rejects.toThrow("no larger than 5 MB");
    expect(fetchMock).not.toHaveBeenCalled(); expect(apiPostForm).not.toHaveBeenCalled();
  });
  it("does not send 5 MiB through BFF when the feature flag is disabled",async()=>{
    vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED","false");
    await expect(uploadResumeFile(file(MAX_RESUME_BYTES))).rejects.toThrow("cannot pass through the current website upload route");
    expect(fetchMock).not.toHaveBeenCalled(); expect(apiPostForm).not.toHaveBeenCalled();
  });
  it("preserves the native TeX path, original File and bounded legacy upload",async()=>{
    const value=file(MAX_LEGACY_RESUME_BYTES,"resume.tex","application/x-tex");
    vi.mocked(apiPostForm).mockResolvedValue(resume);
    expect(await uploadResumeFile(value,{enrichSkills:true})).toEqual(resume);
    const [path,form]=vi.mocked(apiPostForm).mock.calls[0];
    expect(path).toBe("/resume/parse"); expect(form.get("file")).toBe(value); expect(form.get("enrich_skills")).toBe("true"); expect(fetchMock).not.toHaveBeenCalled();
  });
  it("legacy cancellation stops waiting and warns that an original may have been saved",async()=>{
    vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED","false");
    vi.mocked(apiPostForm).mockReturnValue(new Promise(()=>undefined));
    const controller=new AbortController(); const result=uploadResumeFile(file(),{signal:controller.signal}); controller.abort();
    await expect(result).rejects.toBeInstanceOf(ResumeUploadCancelled); await expect(result).rejects.toMatchObject({cleanup:"legacy"});
  });
});
