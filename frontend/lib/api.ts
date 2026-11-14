import { accountApiPath, accountMutationHeaders } from "./accountTransportClient";
import { retryAfterMilliseconds } from "./boundedStatusPolling";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown,
    public readonly retryAfterMs: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseResponse(res: Response) {
  const raw = await res.text();
  const contentType = res.headers.get("content-type") || "";
  if (raw && contentType.includes("application/json")) {
    try {
      return JSON.parse(raw);
    } catch {
      return { detail: raw };
    }
  }
  return raw ? { detail: raw } : {};
}

function errorMessage(data: unknown, fallback: string): string {
  if (typeof data === "string" && data) return data;
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const target = accountApiPath(path);
  if (target.startsWith("/api/account/") && ["POST", "PUT", "PATCH", "DELETE"].includes(init.method || "GET")) {
    init = { ...init, headers: { ...await accountMutationHeaders(), ...init.headers } };
  }
  const res = await fetch(target, {
    cache: "no-store",
    ...init,
  });
  const data = await parseResponse(res);
  if (!res.ok) {
    throw new ApiError(
      errorMessage(data, `${init.method || "GET"} ${path} failed (${res.status})`),
      res.status,
      data,
      res.status === 429 || res.status === 503 ? retryAfterMilliseconds(res.headers.get("Retry-After"), res.status) : null,
    );
  }
  return data as T;
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal });
}

export async function apiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(accountApiPath(path), { cache: "no-store", signal });
  if (!response.ok) {
    const data = await parseResponse(response);
    throw new ApiError(errorMessage(data, `File request failed (${response.status})`), response.status, data);
  }
  return response.blob();
}

export async function apiPostJson<T>(
  path: string,
  body: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

export async function apiPutJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiPatchJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiDelete<T = void>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: form,
  });
}

type ReviewedDownload = { media_type: string; size_bytes: number; sha256: string };

export async function apiDownload(path: string, fallbackFilename: string, reviewed?: ReviewedDownload, signal?: AbortSignal): Promise<void> {
  const response = await fetch(accountApiPath(path), { cache: "no-store", signal, ...(reviewed ? { redirect: "error" } : {}) });
  if (!response.ok) {
    const data = await parseResponse(response);
    throw new ApiError(errorMessage(data, `Download failed (${response.status})`), response.status, data);
  }
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = reviewed ? fallbackFilename : match?.[1] || fallbackFilename;
  const blob = await response.blob();
  if (reviewed) {
    const mismatch = () => new Error("This file no longer matches the reviewed application. Load a fresh review from your companion.");
    if (blob.size !== reviewed.size_bytes || blob.type !== reviewed.media_type) throw mismatch();
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (hash !== reviewed.sha256) throw mismatch();
  }
  signal?.throwIfAborted();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
