import { accountApiPath } from "./accountTransportClient";
import { apiPostForm } from "./api";
import type { ResumeParseResponse } from "./types";

export const MAX_RESUME_BYTES = 5 * 1024 * 1024;
export const MAX_LEGACY_RESUME_BYTES = 4 * 1024 * 1024; // leave multipart headroom below Vercel's 4.5 MB limit
const MAX_WAIT_MS = 240_000;
const CONTROL_TIMEOUT_MS = 10_000;
const POLL_MS = 2000;
const PDF = "application/pdf";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const STATES = new Set(["awaiting_upload", "queued", "inspecting", "released", "rejected", "failed", "cancelled", "expired"]);
const FORMATS = new Set([PDF, DOCX, "application/x-tex", "text/x-tex", "text/plain", "application/zip", "application/x-zip-compressed", "application/octet-stream", ""]);

export type UploadPhase = "validating" | "hashing" | "creating" | "uploading" | "reconciling" | "queued" | "inspecting" | "completed" | "cancelling";
export type UploadProgress = { phase: UploadPhase; mode: "direct" | "legacy" };
export const uploadPhaseLabels: Record<UploadPhase, string> = {
  validating: "Checking your file…", hashing: "Preparing a secure upload…", creating: "Reserving private upload space…",
  uploading: "Uploading your original file…", reconciling: "Checking whether your upload arrived…",
  queued: "Waiting for resume safety checks…", inspecting: "Checking and extracting your resume…",
  completed: "Resume saved", cancelling: "Cancelling this upload…",
};

export function directResumeUploadEnabled(): boolean {
  return process.env.NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED === "true";
}

export function usesDirectResumeUpload(file: File): boolean {
  return directResumeUploadEnabled() && /\.(pdf|docx)$/i.test(file.name);
}

export class ResumeUploadCancelled extends Error {
  constructor(public readonly cleanup: "requested" | "already_saved" | "unconfirmed" | "not_started" | "legacy") {
    super(cleanup === "already_saved" ? "Your resume finished saving before cancellation. Review your saved resumes."
      : cleanup === "legacy" ? "Stopped waiting. This upload may already have been saved; check your resume list before trying again."
      : cleanup === "not_started" ? "Upload cancelled before a file was sent."
      : cleanup === "unconfirmed" ? "Stopped waiting. Cleanup could not be confirmed; check your resume list before trying again."
      : "Upload cancelled. Temporary upload cleanup was requested.");
    this.name = "ResumeUploadCancelled";
  }
}

class ControlFailure extends Error {
  constructor(public readonly status: number, public readonly code: string | null = null) { super("Secure resume upload is unavailable. Please try again later."); }
}

type Intent = { upload_id: string; state: string; upload_url: string | null; method: "PUT"; headers: Record<string, string>; upload_grant_expires_at: string; expires_at: string };
type Status = { upload_id: string; state: string; error_code: string | null; resume: ResumeParseResponse | null };
type Options = { signal?: AbortSignal; enrichSkills?: boolean; onProgress?: (progress: UploadProgress) => void };

function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function stopped(signal: AbortSignal): void { if (signal.aborted) throw new DOMException("Upload stopped", "AbortError"); }

export function validateResumeFile(file: File): void {
  if (!/\.(pdf|docx|tex|zip)$/i.test(file.name) || !FORMATS.has(file.type) || file.size <= 0 || file.size > MAX_RESUME_BYTES) {
    throw new Error("Choose PDF, DOCX, TeX or a source ZIP project no larger than 5 MB.");
  }
  if (!usesDirectResumeUpload(file) && file.size > MAX_LEGACY_RESUME_BYTES) {
    throw new Error("Secure direct upload is not enabled for this file format yet. Use a file up to 4 MB; larger files cannot pass through the current website upload route.");
  }
}

async function jsonResponse(response: Response): Promise<unknown> {
  if (!response.headers.get("content-type")?.split(";")[0].includes("application/json")) throw new Error("Resume upload returned an invalid response.");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Resume upload returned an invalid response.");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.length;
      if (size > 1024 * 1024) throw new Error("Resume upload returned an invalid response.");
      chunks.push(next.value);
    }
    const output = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
    try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(output)); }
    catch { throw new Error("Resume upload returned an invalid response."); }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

async function control(path: string, method: string, signal: AbortSignal, body?: unknown, headers: Record<string, string> = {}): Promise<unknown> {
  stopped(signal);
  const response = await fetch(accountApiPath(path), { method, credentials: "same-origin", redirect: "error", cache: "no-store",
    signal: AbortSignal.any([signal, AbortSignal.timeout(CONTROL_TIMEOUT_MS)]),
    headers: { ...headers, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) {
    let code: string | null = null;
    try {
      const error = await jsonResponse(response);
      if (record(error) && record(error.detail) && typeof error.detail.code === "string" && /^[a-z_]{1,64}$/.test(error.detail.code)) code = error.detail.code;
    } catch { /* Discard error bodies; never display source content or URLs. */ }
    throw new ControlFailure(response.status, code);
  }
  return jsonResponse(response);
}

function validateIntent(value: unknown, file: File, media: string): Intent {
  if (!record(value) || typeof value.upload_id !== "string" || !/^rup_[a-f0-9]{32}$/.test(value.upload_id)
    || typeof value.state !== "string" || !STATES.has(value.state) || value.method !== "PUT"
    || typeof value.upload_grant_expires_at !== "string" || typeof value.expires_at !== "string"
    || !Number.isFinite(Date.parse(value.expires_at)) || !Number.isFinite(Date.parse(value.upload_grant_expires_at))
    || !record(value.headers)) throw new Error("Secure upload instructions are invalid.");
  if (value.state === "awaiting_upload") {
    if (typeof value.upload_url !== "string" || value.upload_url.length > 8192) throw new Error("Secure upload instructions are invalid.");
    let url: URL;
    try { url = new URL(value.upload_url); } catch { throw new Error("Secure upload instructions are invalid."); }
    if (url.protocol !== "https:" || url.hostname !== "storage.googleapis.com" || url.port || url.username || url.password || url.hash
      || !new RegExp(`^/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/quarantine/${value.upload_id}/source$`).test(url.pathname)
      || url.searchParams.get("X-Goog-Algorithm") !== "GOOG4-RSA-SHA256"
      || url.searchParams.get("X-Goog-SignedHeaders") !== "content-type;host;x-goog-content-length-range;x-goog-if-generation-match"
      || !/^[1-9][0-9]{0,2}$/.test(url.searchParams.get("X-Goog-Expires") || "") || Number(url.searchParams.get("X-Goog-Expires")) > 300
      || !/^[a-f0-9]{128,2048}$/.test(url.searchParams.get("X-Goog-Signature") || "")
      || Object.keys(value.headers).sort().join(",") !== "Content-Type,x-goog-content-length-range,x-goog-if-generation-match"
      || value.headers["Content-Type"] !== media || value.headers["x-goog-if-generation-match"] !== "0"
      || value.headers["x-goog-content-length-range"] !== `${file.size},${file.size}`
      || Date.parse(value.upload_grant_expires_at) <= Date.now()) throw new Error("Secure upload instructions are invalid.");
  } else if (value.upload_url !== null) throw new Error("Secure upload instructions are invalid.");
  return value as unknown as Intent;
}

function validateStatus(value: unknown, id: string, sourceFormat: "pdf" | "docx"): Status {
  if (!record(value) || value.upload_id !== id || typeof value.state !== "string" || !STATES.has(value.state)
    || (value.error_code !== null && (typeof value.error_code !== "string" || value.error_code.length > 64))) throw new Error("Resume status could not be verified.");
  if (value.state === "released" && (!record(value.resume) || !Number.isSafeInteger(value.resume.resume_id) || Number(value.resume.resume_id) <= 0
    || value.resume.source_available !== true || value.resume.source_format !== sourceFormat || !Array.isArray(value.resume.skills))) {
    throw new Error("Saved resume could not be verified.");
  }
  return value as unknown as Status;
}

function canReconcile(error: unknown): boolean {
  return (error instanceof ControlFailure && error.status >= 500) || error instanceof TypeError
    || (error instanceof DOMException && ["TimeoutError", "AbortError"].includes(error.name));
}

async function delay(signal: AbortSignal): Promise<void> {
  stopped(signal);
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, POLL_MS);
    function abort() { clearTimeout(timer); reject(new DOMException("Upload stopped", "AbortError")); }
    signal.addEventListener("abort", abort, { once: true });
  });
}

async function cancelIntent(id: string): Promise<ResumeUploadCancelled["cleanup"]> {
  try {
    const acknowledgment = await control(`/resume/uploads/${id}`, "DELETE", AbortSignal.timeout(CONTROL_TIMEOUT_MS));
    return record(acknowledgment) && Object.keys(acknowledgment).sort().join(",") === "error_code,resume,state,upload_id"
      && acknowledgment.upload_id === id && acknowledgment.state === "cancelled" && acknowledgment.resume === null
      && (acknowledgment.error_code === null || (typeof acknowledgment.error_code === "string" && acknowledgment.error_code.length <= 64))
      ? "requested" : "unconfirmed";
  }
  catch (error) { return error instanceof ControlFailure && error.status === 409 && error.code === "saved_resume_requires_deletion" ? "already_saved" : "unconfirmed"; }
}

async function legacyUpload(file: File, options: Options, signal: AbortSignal): Promise<ResumeParseResponse> {
  const form = new FormData(); form.append("file", file); form.append("enrich_skills", String(options.enrichSkills ?? false));
  options.onProgress?.({ phase: "uploading", mode: "legacy" });
  // This established endpoint cannot revoke an in-flight parse. Cancellation
  // stops waiting and prevents stale UI updates; the saved result may remain.
  return new Promise((resolve, reject) => {
    function abort() { reject(new ResumeUploadCancelled("legacy")); }
    signal.addEventListener("abort", abort, { once: true });
    apiPostForm<ResumeParseResponse>("/resume/parse", form).then((result) => {
      signal.removeEventListener("abort", abort);
      if (signal.aborted) reject(new ResumeUploadCancelled("legacy")); else resolve(result);
    }, (error) => { signal.removeEventListener("abort", abort); reject(error); });
  });
}

export async function uploadResumeFile(file: File, options: Options = {}): Promise<ResumeParseResponse> {
  validateResumeFile(file);
  const direct = usesDirectResumeUpload(file);
  const signal = AbortSignal.any([...(options.signal ? [options.signal] : []), AbortSignal.timeout(MAX_WAIT_MS)]);
  const progress = (phase: UploadPhase) => { stopped(signal); options.onProgress?.({ phase, mode: direct ? "direct" : "legacy" }); stopped(signal); };
  progress("validating");
  if (!direct) {
    const result = await legacyUpload(file, options, signal); stopped(signal); progress("completed"); return result;
  }
  let id: string | null = null;
  try {
    progress("hashing");
    if (!globalThis.crypto?.subtle || typeof file.arrayBuffer !== "function") throw new Error("Secure upload requires a modern browser over HTTPS.");
    const bytes = await file.arrayBuffer(); stopped(signal);
    if (bytes.byteLength !== file.size || bytes.byteLength > MAX_RESUME_BYTES) throw new Error("Your file changed. Select it again.");
    const digest = await crypto.subtle.digest("SHA-256", bytes); stopped(signal);
    const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const key = crypto.randomUUID();
    progress("creating");
    const sourceFormat = /\.pdf$/i.test(file.name) ? "pdf" : "docx";
    const media = sourceFormat === "pdf" ? PDF : DOCX;
    const raw = await control("/resume/uploads", "POST", signal,
      { filename: file.name, size_bytes: file.size, sha256, enrich_skills: options.enrichSkills ?? false }, { "Idempotency-Key": key });
    // Remember a valid opaque ID even if the remaining capability is refused.
    if (record(raw) && typeof raw.upload_id === "string" && /^rup_[a-f0-9]{32}$/.test(raw.upload_id)) id = raw.upload_id;
    const intent = validateIntent(raw, file, media); id = intent.upload_id;
    if (intent.state === "awaiting_upload") {
      progress("uploading");
      try {
        const response = await fetch(intent.upload_url!, { method: "PUT", body: file, headers: intent.headers,
          credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store", signal });
        if (!response.ok) progress("reconciling");
      } catch { stopped(signal); progress("reconciling"); }
    }
    // PUT is sent only once. Lost/ambiguous responses are resolved through the
    // owner-bound server verdict; no blind PUT retry or legacy fallback occurs.
    let completionAttempts = 0;
    let current: Status | null = null;
    while (true) {
      stopped(signal);
      if (current === null || current.state === "awaiting_upload") {
        if (++completionAttempts > 3) throw new Error("Your upload could not be queued. Check your saved resumes before trying again.");
        try { current = validateStatus(await control(`/resume/uploads/${id}/complete`, "POST", signal), id, sourceFormat); }
        catch (error) { stopped(signal); if (!canReconcile(error)) throw error; }
      }
      if (current?.state === "released") { progress("completed"); return current.resume!; }
      if (current && ["rejected", "failed", "cancelled", "expired"].includes(current.state)) {
        throw new Error(current.state === "rejected" ? "This document did not pass resume safety checks. Use a plain PDF or DOCX without active content or encryption. No analysis units were charged."
          : "Your resume could not be saved. No analysis units were charged. Temporary upload cleanup is scheduled.");
      }
      progress(current?.state === "inspecting" ? "inspecting" : "queued");
      await delay(signal);
      try { current = validateStatus(await control(`/resume/uploads/${id}`, "GET", signal), id, sourceFormat); }
      catch (error) { stopped(signal); if (!canReconcile(error)) throw error; }
    }
  } catch (error) {
    const cleanup = id ? await cancelIntent(id) : "not_started";
    if (options.signal?.aborted) throw new ResumeUploadCancelled(cleanup);
    if (signal.aborted) throw new Error(cleanup === "requested"
      ? "Resume checks took too long. Temporary upload cleanup was requested; check your saved resumes before trying again."
      : cleanup === "already_saved" ? "Resume checks took too long. Your resume finished saving before cleanup. Review your saved resumes."
      : cleanup === "not_started" ? "Resume checks took too long before a file was sent. Try again."
      : "Resume checks took too long. Cleanup could not be confirmed; check your saved resumes before trying again.");
    throw error;
  }
}
