/** Owner-authenticated metadata, then one short-lived exact-generation GCS GET.
 * Signed capabilities stay transient; fixed errors never retain URL/provider detail.
 */
export const MAX_ORIGINAL_SOURCE_BYTES = 5 * 1024 * 1024;
export const ORIGINAL_SOURCE_DEADLINE_MS = 45_000;
const CONTROL_BYTES = 8192;
const FAILURE = "Could not verify the original resume file. Please try again.";
const MEDIA = { pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" } as const;
type Format = keyof typeof MEDIA;
export type VerifiedResumeSource = { blob: Blob; filename: string };
type Binding = { url: string; expires_at: string; sha256: string; size_bytes: number; filename: string; media_type: string };

export function directResumeSourceEnabled(): boolean {
  return process.env.NEXT_PUBLIC_RESUME_DIRECT_UPLOAD_ENABLED === "true";
}

function refused(): Error { return new Error(FAILURE); }
function cancelled(): DOMException { return new DOMException("Resume source request cancelled.", "AbortError"); }

// A small flat/nested-object JSON contract, without duplicate-key last-wins parsing.
// Only success scalars and the exact two-level missing-binding error are needed.
function parseControl(raw: string): Record<string, unknown> {
  let offset = 0;
  function space() { while (/[ \t\r\n]/.test(raw[offset] || "") && offset < raw.length) offset++; }
  function string(): string {
    const match = /^"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/.exec(raw.slice(offset));
    if (!match) throw refused();
    offset += match[0].length;
    return JSON.parse(match[0]) as string;
  }
  function object(depth: number): Record<string, unknown> {
    if (depth > 2 || raw[offset++] !== "{") throw refused();
    const result: Record<string, unknown> = Object.create(null);
    const keys = new Set<string>();
    space();
    if (raw[offset] === "}") { offset++; return result; }
    while (true) {
      space(); const key = string(); space();
      if (keys.has(key) || raw[offset++] !== ":") throw refused();
      keys.add(key); space();
      if (raw[offset] === '"') result[key] = string();
      else if (raw[offset] === "{") result[key] = object(depth + 1);
      else {
        const number = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(raw.slice(offset));
        if (!number) throw refused();
        offset += number[0].length; result[key] = Number(number[0]);
      }
      space();
      if (raw[offset] === "}") { offset++; break; }
      if (raw[offset++] !== ",") throw refused();
    }
    return result;
  }
  space(); const data = object(1); space();
  if (offset !== raw.length) throw refused();
  return data;
}

async function readBounded(response: Response, limit: number, signal: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  if (!response.body) throw refused();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let complete = false;
  const onAbort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      if (!ArrayBuffer.isView(value) || Object.prototype.toString.call(value) !== "[object Uint8Array]" || size + value.byteLength > limit) throw refused();
      size += value.byteLength; chunks.push(value);
    }
    const bytes = new Uint8Array(new ArrayBuffer(size));
    let at = 0;
    for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength; }
    complete = true;
    return bytes;
  } finally {
    signal.removeEventListener("abort", onAbort);
    if (!complete) onAbort();
    reader.releaseLock();
  }
}

function exactKeys(data: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(data);
  return actual.length === expected.length && actual.every((key) => expected.includes(key));
}

function validFilename(value: unknown, kind: Format): value is string {
  return typeof value === "string" && value.trim() === value && value.length > 0
    && new TextEncoder().encode(value).length <= 255
    && !/[\\/\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]/.test(value)
    && value.toLowerCase().endsWith(`.${kind}`);
}

function validate(data: Record<string, unknown>, expectedFormat?: Format): { binding: Binding; expiry: number } {
  if (!exactKeys(data, ["url", "expires_at", "sha256", "size_bytes", "filename", "media_type"])
    || typeof data.url !== "string" || data.url.length > 4096
    || typeof data.expires_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)$/.test(data.expires_at)
    || typeof data.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(data.sha256)
    || typeof data.size_bytes !== "number" || !Number.isSafeInteger(data.size_bytes)
    || data.size_bytes < 1 || data.size_bytes > MAX_ORIGINAL_SOURCE_BYTES) throw refused();
  const kind = data.media_type === MEDIA.pdf ? "pdf" : data.media_type === MEDIA.docx ? "docx" : null;
  if (!kind || (expectedFormat && expectedFormat !== kind) || !validFilename(data.filename, kind)) throw refused();
  // Check the raw path too: URL parsing must not hide encoded/dot-segment aliases.
  if (!/^https:\/\/storage\.googleapis\.com\/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\/clean\/rup_[a-f0-9]{32}\/source\?/.test(data.url)) throw refused();
  const target = new URL(data.url);
  if (target.origin !== "https://storage.googleapis.com" || target.username || target.password || target.port || target.hash) throw refused();
  const expected = ["X-Goog-Algorithm", "X-Goog-Credential", "X-Goog-Date", "X-Goog-Expires", "X-Goog-SignedHeaders", "X-Goog-Signature", "generation", "response-content-type"];
  const keys = [...target.searchParams.keys()];
  if (keys.length !== expected.length || new Set(keys).size !== keys.length || keys.some((key) => !expected.includes(key))) throw refused();
  const query = target.searchParams;
  const generation = query.get("generation")!;
  const date = query.get("X-Goog-Date")!;
  const ttl = query.get("X-Goog-Expires")!;
  if (query.get("X-Goog-Algorithm") !== "GOOG4-RSA-SHA256" || query.get("X-Goog-SignedHeaders") !== "host"
    || query.get("response-content-type") !== data.media_type
    || !/^[1-9][0-9]{0,19}$/.test(generation) || BigInt(generation) > 18446744073709551615n
    || !/^\d{8}T\d{6}Z$/.test(date) || !/^[1-9][0-9]?$/.test(ttl) || Number(ttl) > 60
    || !/^(?:[a-f0-9]{512}|[a-f0-9]{768}|[a-f0-9]{1024})$/.test(query.get("X-Goog-Signature")!)) throw refused();
  const credential = query.get("X-Goog-Credential")!;
  const scope = credential.match(/^[a-z][a-z0-9-]{4,28}@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com\/(\d{8})\/auto\/storage\/goog4_request$/);
  if (!scope || scope[1] !== date.slice(0, 8)) throw refused();
  const issued = Date.parse(`${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T${date.slice(9, 11)}:${date.slice(11, 13)}:${date.slice(13, 15)}Z`);
  const expiry = Date.parse(data.expires_at);
  const signedExpiry = issued + Number(ttl) * 1000;
  if (!Number.isFinite(issued) || new Date(issued).toISOString().replace(/[-:]|\.000/g, "") !== date
    || !Number.isFinite(expiry) || expiry <= Date.now() || expiry > Date.now() + 90_000
    || issued > Date.now() + 30_000 || signedExpiry <= Date.now()
    || Math.abs(expiry - signedExpiry) > 2000) throw refused();
  return { binding: data as Binding, expiry: Math.min(expiry, signedExpiry) };
}

export async function fetchDirectResumeSource(resumeId: number, signal?: AbortSignal, expectedFormat?: Format): Promise<VerifiedResumeSource | null> {
  if (!Number.isSafeInteger(resumeId) || resumeId < 1) throw refused();
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(onAbort, ORIGINAL_SOURCE_DEADLINE_MS);
  let abortListener: (() => void) | undefined;
  const abortPromise = new Promise<never>((_, reject) => {
    abortListener = () => reject(cancelled());
    controller.signal.addEventListener("abort", abortListener, { once: true });
    if (controller.signal.aborted) reject(cancelled());
  });
  const work = async () => {
    controller.signal.throwIfAborted();
    const response = await fetch(`/api/backend/resume/${resumeId}/source-access`, {
      method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal,
    });
    controller.signal.throwIfAborted();
    if (response.redirected || response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") throw refused();
    const raw = await readBounded(response, CONTROL_BYTES, controller.signal);
    const data = parseControl(new TextDecoder("utf-8", { fatal: true }).decode(raw));
    if (response.status === 404 && exactKeys(data, ["detail"]) && data.detail && typeof data.detail === "object"
      && exactKeys(data.detail as Record<string, unknown>, ["code"]) && (data.detail as Record<string, unknown>).code === "direct_source_not_found") return null;
    if (response.status !== 200) throw refused();
    const { binding, expiry } = validate(data, expectedFormat);
    controller.signal.throwIfAborted();
    const source = await fetch(binding.url, { method: "GET", credentials: "omit", referrerPolicy: "no-referrer", cache: "no-store", redirect: "error", signal: controller.signal });
    controller.signal.throwIfAborted();
    if (source.status !== 200 || source.redirected || source.url !== binding.url
      || source.headers.get("content-type")?.trim().toLowerCase() !== binding.media_type) throw refused();
    const length = source.headers.get("content-length");
    if (length !== null && (!/^[1-9][0-9]*$/.test(length) || Number(length) !== binding.size_bytes)) throw refused();
    const bytes = await readBounded(source, binding.size_bytes, controller.signal);
    if (bytes.byteLength !== binding.size_bytes) throw refused();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    controller.signal.throwIfAborted();
    const actual = Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
    if (actual !== binding.sha256 || Date.now() >= expiry) throw refused();
    return { blob: new Blob([bytes], { type: binding.media_type }), filename: binding.filename };
  };
  try { return await Promise.race([work(), abortPromise]); }
  catch { if (controller.signal.aborted) throw cancelled(); throw refused(); }
  finally {
    clearTimeout(timer); signal?.removeEventListener("abort", onAbort);
    if (abortListener) controller.signal.removeEventListener("abort", abortListener);
    controller.abort();
  }
}

export function saveVerifiedResumeSource(source: VerifiedResumeSource, signal: AbortSignal): void {
  signal.throwIfAborted();
  const kind = source.blob.type === MEDIA.pdf ? "pdf" : source.blob.type === MEDIA.docx ? "docx" : null;
  if (!kind || !validFilename(source.filename, kind) || source.blob.size < 1 || source.blob.size > MAX_ORIGINAL_SOURCE_BYTES) throw refused();
  const url = URL.createObjectURL(source.blob);
  try {
    signal.throwIfAborted();
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = source.filename; anchor.click();
  } finally { setTimeout(() => URL.revokeObjectURL(url), 0); }
}
