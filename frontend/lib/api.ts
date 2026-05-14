const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

function withAuth(headers: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
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

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    headers: withAuth(),
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `GET ${path} failed (${res.status})`);
  return data as T;
}

export async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: withAuth({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `POST ${path} failed (${res.status})`);
  return data as T;
}

export async function apiPutJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: withAuth({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `PUT ${path} failed (${res.status})`);
  return data as T;
}

export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: withAuth(), // don't set Content-Type; browser sets boundary
    body: form,
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `POST ${path} failed (${res.status})`);
  return data as T;
}
