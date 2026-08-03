const API_BASE = "/api/backend";
const POLICY_VERSION = "2026-07-11";

export async function prepareGoogleRegistrationConsent() {
  const res = await fetch("/api/auth/google-consent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      accepted_terms: true,
      confirmed_age_18: true,
      policy_version: POLICY_VERSION,
    }),
  });
  if (!res.ok) throw new Error("Could not record registration consent.");
}

export async function register(email: string, password: string) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      accepted_terms: true,
      confirmed_age_18: true,
    }),
  });
  const txt = await res.text();
  const json = txt ? (() => { try { return JSON.parse(txt); } catch { return null; } })() : null;
  if (!res.ok) throw new Error(json?.detail || txt || `Register failed (${res.status})`);
  return json;
}
