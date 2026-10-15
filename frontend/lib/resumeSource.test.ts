import { Blob as BrowserBlob } from "node:buffer";
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { fetchDirectResumeSource, saveVerifiedResumeSource } from "./resumeSource";

const NOW = Date.parse("2026-10-10T12:00:00Z");
const PDF = new TextEncoder().encode("%PDF-synthetic-source");
let calls: Array<{ url: string; init?: RequestInit }>;

async function binding(bytes = PDF) {
  const hash = Array.from(new Uint8Array(await webcrypto.subtle.digest("SHA-256", bytes))).map((x) => x.toString(16).padStart(2, "0")).join("");
  const query = new URLSearchParams({
    "X-Goog-Algorithm": "GOOG4-RSA-SHA256", "X-Goog-Credential": "read-signer@synthetic-project.iam.gserviceaccount.com/20261010/auto/storage/goog4_request",
    "X-Goog-Date": "20261010T120000Z", "X-Goog-Expires": "60", "X-Goog-SignedHeaders": "host",
    "X-Goog-Signature": "a".repeat(512), generation: "123", "response-content-type": "application/pdf",
  });
  return { url: `https://storage.googleapis.com/synthetic-clean/clean/rup_${"a".repeat(32)}/source?${query}`,
    expires_at: "2026-10-10T12:01:00Z", sha256: hash, size_bytes: bytes.length,
    filename: "original.pdf", media_type: "application/pdf" };
}

function fileResponse(bytes: Uint8Array, url: string) {
  const response = new Response(new Uint8Array(bytes).buffer, { headers: { "Content-Type": "application/pdf", "Content-Length": String(bytes.length) } });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function responses(meta: Awaited<ReturnType<typeof binding>>, bytes = PDF) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return calls.length === 1 ? Response.json(meta) : fileResponse(bytes, meta.url);
  }));
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("Blob", BrowserBlob);
  vi.spyOn(Date, "now").mockReturnValue(NOW);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("gets one owner-authenticated control and one secretless generation-pinned file", async () => {
  const meta = await binding(); responses(meta);
  const file = await fetchDirectResumeSource(7, undefined, "pdf");
  expect(file?.filename).toBe("original.pdf");
  expect(await file?.blob.text()).toBe(new TextDecoder().decode(PDF));
  expect(calls).toHaveLength(2);
  expect(calls[0].url).toBe("/api/backend/resume/7/source-access");
  expect(calls[0].init).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error" });
  expect(calls[1].init).toMatchObject({ credentials: "omit", referrerPolicy: "no-referrer", cache: "no-store", redirect: "error", method: "GET" });
  expect(calls[1].init?.headers).toBeUndefined();
});

it("verifies exactly five MiB before returning a blob", async () => {
  const bytes = new Uint8Array(5 * 1024 * 1024); bytes.set(PDF);
  const meta = await binding(bytes); responses(meta, bytes);
  const file = await fetchDirectResumeSource(7, undefined, "pdf");
  expect(file?.blob.size).toBe(bytes.length);
  expect(Buffer.compare(Buffer.from(await file!.blob.arrayBuffer()), Buffer.from(bytes))).toBe(0);
});

it("only exact missing-direct-binding404 permits legacy fallback", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: { code: "direct_source_not_found" } }, { status: 404 })));
  expect(await fetchDirectResumeSource(7)).toBeNull();
});

it.each([401, 403, 409, 503])("never falls back after HTTP%s", async (status) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "private signedurl token" }, { status })));
  await expect(fetchDirectResumeSource(7)).rejects.toThrow("Could not verify the original resume file.");
});

it("does not fall back from generic404 or foreign-owner404", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: { code: "resume_not_found" } }, { status: 404 })));
  await expect(fetchDirectResumeSource(7)).rejects.toThrow();
});

it("refuses hash mismatch without a second storage request or URL reflection", async () => {
  const meta = await binding(); responses(meta, new Uint8Array(PDF.length));
  await expect(fetchDirectResumeSource(7)).rejects.toThrow("Could not verify the original resume file.");
  expect(calls).toHaveLength(2);
});

it.each([
  ["size_bytes", 0], ["size_bytes", 5 * 1024 * 1024 + 1], ["size_bytes", "20"],
  ["sha256", "A".repeat(64)], ["filename", "../original.pdf"], ["filename", "original.docx"],
  ["filename", "bad\n.pdf"], ["filename", "x".repeat(252) + ".pdf"], ["media_type", "text/html"],
  ["expires_at", "2026-10-10T11:59:59Z"], ["expires_at", "2026-10-10T12:05:00Z"],
])("rejects invalid bounded metadata %s without storage access", async (key, value) => {
  const meta = await binding(); responses({ ...meta, [key]: value } as typeof meta);
  await expect(fetchDirectResumeSource(7)).rejects.toThrow("Could not verify the original resume file.");
  expect(calls).toHaveLength(1);
});

it.each([
  (url: string) => url.replace("storage.googleapis.com", "storage.googleapis.com.untrusted.invalid"),
  (url: string) => url.replace("https://", "http://"),
  (url: string) => url.replace("storage.googleapis.com/", "user@storage.googleapis.com/"),
  (url: string) => url.replace("storage.googleapis.com/", "storage.googleapis.com:443/"),
  (url: string) => url.replace("/clean/", "/quarantine/"),
  (url: string) => url.replace("/clean/", "/alias/../clean/"),
  (url: string) => url.replace("/clean/", "/%63lean/"),
  (url: string) => url + "&generation=456",
  (url: string) => url + "&extra=private",
  (url: string) => url + "#fragment",
  (url: string) => url.replace("generation=123", "generation=0"),
  (url: string) => url.replace("generation=123", "generation=18446744073709551616"),
  (url: string) => url.replace("X-Goog-Expires=60", "X-Goog-Expires=600"),
  (url: string) => url.replace("X-Goog-SignedHeaders=host", "X-Goog-SignedHeaders=host%3Bauthorization"),
  (url: string) => url.replace("X-Goog-Date=20261010", "X-Goog-Date=20261011"),
])("rejects untrusted origin/path/V4 query or replay before fetching file", async (change) => {
  const meta = await binding(); responses({ ...meta, url: change(meta.url) });
  await expect(fetchDirectResumeSource(7)).rejects.toThrow("Could not verify the original resume file.");
  expect(calls).toHaveLength(1);
});

it("rejects duplicate top-level or nested fallback keys", async () => {
  const meta = await binding();
  for (const raw of [JSON.stringify(meta).replace('"size_bytes":', '"size_bytes":1,"size_bytes":'),
    '{"detail":{"code":"resume_not_found","code":"direct_source_not_found"}}']) {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(raw, { status: 404, headers: { "Content-Type": "application/json" } })));
    await expect(fetchDirectResumeSource(7)).rejects.toThrow();
  }
});

it("valid DOCX has matching filename, media and response-content-type", async () => {
  const meta = await binding();
  const url = new URL(meta.url);
  url.searchParams.set("response-content-type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  const doc = { ...meta, url: url.href, filename: "original.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => {
    calls.push({ url: uri });
    if (calls.length === 1) return Response.json(doc);
    const response = fileResponse(PDF, doc.url); response.headers.set("Content-Type", doc.media_type); return response;
  }));
  const source = await fetchDirectResumeSource(7, undefined, "docx");
  expect(source?.blob.type).toBe(doc.media_type);
  expect(source?.filename).toBe("original.docx");
});

it("expected PDF cannot accept a DOCX binding", async () => {
  const meta = await binding(); responses(meta);
  await expect(fetchDirectResumeSource(7, undefined, "docx")).rejects.toThrow();
  expect(calls).toHaveLength(1);
});

it("storage denial or redirect never retries and never reflects provider detail", async () => {
  const meta = await binding();
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => {
    calls.push({ url: uri });
    return calls.length === 1 ? Response.json(meta) : new Response("private credential signedurl", { status: 403 });
  }));
  await expect(fetchDirectResumeSource(7)).rejects.toThrow("Could not verify the original resume file.");
  expect(calls).toHaveLength(2);
});

it("reads at most declared bytes and cancels an oversized body", async () => {
  const meta = await binding(); let cancelled = false;
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => {
    calls.push({ url: uri }); if (calls.length === 1) return Response.json(meta);
    const response = new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(PDF.length + 1)); }, cancel() { cancelled = true; } }), { headers: { "Content-Type": "application/pdf" } });
    Object.defineProperty(response, "url", { value: meta.url }); return response;
  }));
  await expect(fetchDirectResumeSource(7)).rejects.toThrow();
  expect(cancelled).toBe(true);
});

it("rejects short body, changed content length and changed media", async () => {
  const meta = await binding();
  for (const kind of ["short", "length", "media"]) {
    let count = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (++count === 1) return Response.json(meta);
      const response = fileResponse(kind === "short" ? PDF.slice(1) : PDF, meta.url);
      if (kind === "length") response.headers.set("Content-Length", "99999");
      if (kind === "media") response.headers.set("Content-Type", "text/html");
      return response;
    }));
    await expect(fetchDirectResumeSource(7)).rejects.toThrow(); expect(count).toBe(2);
  }
});

it("pre-aborted owners never request a capability", async () => {
  const controller = new AbortController(); controller.abort();
  vi.stubGlobal("fetch", vi.fn());
  await expect(fetchDirectResumeSource(7, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(fetch).not.toHaveBeenCalled();
});

it("deadline cancels a stalled response even when the synthetic fetch ignores its signal", async () => {
  const meta = await binding(); vi.useFakeTimers(); let cancelled = false;
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => {
    calls.push({ url: uri }); if (calls.length === 1) return Response.json(meta);
    const response = new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { "Content-Type": "application/pdf" } });
    Object.defineProperty(response, "url", { value: meta.url }); return response;
  }));
  const operation = fetchDirectResumeSource(7);
  const assertion = expect(operation).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(45_000); await assertion;
  expect(cancelled).toBe(true); expect(calls).toHaveLength(2); expect(vi.getTimerCount()).toBe(0);
});

it("owner cancellation during hashing cannot publish a blob", async () => {
  const meta = await binding(); responses(meta);
  let finish!: (value: ArrayBuffer) => void;
  const digest = new Promise<ArrayBuffer>((resolve) => { finish = resolve; });
  const started = vi.fn(() => digest);
  vi.stubGlobal("crypto", { subtle: { digest: started } });
  const controller = new AbortController(); const operation = fetchDirectResumeSource(7, controller.signal);
  const assertion = expect(operation).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => expect(started).toHaveBeenCalledOnce()); controller.abort(); await assertion;
  finish(new ArrayBuffer(32));
});

it("download releases blob URL and stale callers cannot create one", async () => {
  const create = vi.fn(() => "blob:owned"); const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  vi.useFakeTimers(); const controller = new AbortController();
  saveVerifiedResumeSource({ blob: new Blob([PDF], { type: "application/pdf" }), filename: "original.pdf" }, controller.signal);
  expect(create).toHaveBeenCalledOnce(); await vi.runAllTimersAsync(); expect(revoke).toHaveBeenCalledWith("blob:owned");
  controller.abort(); expect(() => saveVerifiedResumeSource({ blob: new Blob([PDF], { type: "application/pdf" }), filename: "original.pdf" }, controller.signal)).toThrow();
  expect(create).toHaveBeenCalledOnce();
});

it("feature remains off by default and old source route remains usable", async () => {
  vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "false");
  const { fetchResumeSource } = await import("./career");
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => {
    calls.push({ url: uri }); return fileResponse(PDF, uri);
  }));
  expect((await fetchResumeSource(7)).size).toBe(PDF.length);
  expect(calls.map((call) => call.url)).toEqual(["/api/backend/resume/7/source"]);
  vi.unstubAllEnvs();
});

it("enabled source preview uses verified direct bytes and exact missing404 fallback only", async () => {
  vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "true");
  const { fetchResumeSource } = await import("./career");
  const meta = await binding(); responses(meta);
  expect((await fetchResumeSource(7, undefined, "pdf")).size).toBe(PDF.length);
  expect(calls).toHaveLength(2);
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => {
    calls.push({ url: uri });
    return calls.length === 1 ? Response.json({ detail: { code: "direct_source_not_found" } }, { status: 404 }) : fileResponse(PDF, uri);
  }));
  expect((await fetchResumeSource(7, undefined, "pdf")).size).toBe(PDF.length);
  expect(calls.map((call) => call.url)).toEqual(["/api/backend/resume/7/source-access", "/api/backend/resume/7/source"]);
  vi.unstubAllEnvs();
});

it("tailored and TeX source requests remain on the existing version/source transport", async () => {
  vi.stubEnv("NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED", "true");
  const { fetchResumeSource, fetchResumeVersionPdf } = await import("./career");
  vi.stubGlobal("fetch", vi.fn(async (uri: string) => { calls.push({ url: uri }); return fileResponse(PDF, uri); }));
  await fetchResumeVersionPdf("version/7"); await fetchResumeSource(7, undefined, "tex");
  expect(calls.map((call) => call.url)).toEqual(["/api/backend/v1/resume-versions/version%2F7/download?format=pdf", "/api/backend/resume/7/source"]);
  vi.unstubAllEnvs();
});
