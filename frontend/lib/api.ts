const API_BASE = "/api/backend";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown,
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
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
  });
  const data = await parseResponse(res);
  if (!res.ok) {
    throw new ApiError(
      errorMessage(data, `${init.method || "GET"} ${path} failed (${res.status})`),
      res.status,
      data,
    );
  }
  return data as T;
}

export async function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
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

export async function apiDownload(path: string, fallbackFilename: string): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const data = await parseResponse(response);
    throw new ApiError(errorMessage(data, `Download failed (${response.status})`), response.status, data);
  }
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] || fallbackFilename;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
