import { getSession } from "next-auth/react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

async function getToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const session = await getSession();
  if ((session as any)?.user?.accessToken) {
    return (session as any).user.accessToken;
  }
  return null;
}

async function withAuth(headers: Record<string, string> = {}): Promise<Record<string, string>> {
  const token = await getToken();
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
  const headers = await withAuth();
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    headers,
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `GET ${path} failed (${res.status})`);
  return data as T;
}

export async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  const headers = await withAuth({ "Content-Type": "application/json" });
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `POST ${path} failed (${res.status})`);
  return data as T;
}

export async function apiPutJson<T>(path: string, body: unknown): Promise<T> {
  const headers = await withAuth({ "Content-Type": "application/json" });
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers,
    body: JSON.stringify(body),
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `PUT ${path} failed (${res.status})`);
  return data as T;
}

export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const headers = await withAuth(); // don't set Content-Type; browser sets boundary
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: form,
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `POST ${path} failed (${res.status})`);
  return data as T;
}

