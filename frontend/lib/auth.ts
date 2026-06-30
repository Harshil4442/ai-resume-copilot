// SECURITY NOTE: see lib/api.ts for the localStorage / httpOnly-cookie
// migration backlog item (PRD.md P1).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

export function logout() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("access_token");
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

export async function register(email: string, password: string) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const txt = await res.text();
  const json = txt ? (() => { try { return JSON.parse(txt); } catch { return null; } })() : null;
  if (!res.ok) throw new Error(json?.detail || txt || `Register failed (${res.status})`);
  return json;
}

export async function login(email: string, password: string) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await parseResponse(res);
  if (!res.ok) throw new Error((data as any)?.detail || `Login failed (${res.status})`);
  const token = (data as any)?.access_token;
  if (!token) throw new Error("No access_token returned");
  if (typeof window !== "undefined") localStorage.setItem("access_token", token);
  return data;
}
