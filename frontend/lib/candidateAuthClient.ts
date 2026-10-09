import { accountMutationHeaders } from "./accountTransportClient";

export type RegistrationState = "ENROLLED" | "PENDING_REVIEW_REQUIRED" | "LEGACY_ENROLLMENT_UNAVAILABLE";

export async function candidateRegistration(operation: "register" | "registration-status", email: string, password: string) {
  const result = await fetch(`/api/candidate-account/${operation}`, { method: "POST", cache: "no-store", redirect: "error",
    signal: AbortSignal.timeout(20000), headers: await accountMutationHeaders(),
    body: JSON.stringify({ email, password, ...(operation === "register" ? { accepted_terms: true, confirmed_age_18: true } : {}) }) });
  if (!result.ok) throw new Error(result.status === 401
    ? "The account could not be verified. Check your email and password."
    : "The secure registration outcome is unavailable. Check registration status before trying again.");
  const data = await result.json();
  if (!["ENROLLED", "PENDING_REVIEW_REQUIRED", "LEGACY_ENROLLMENT_UNAVAILABLE"].includes(data.status)) throw new Error("Registration status is unavailable.");
  return data.status as RegistrationState;
}

export async function guardedSignOut() {
  const csrfResponse = await fetch("/api/auth/csrf", { cache: "no-store", signal: AbortSignal.timeout(10000) });
  if (!csrfResponse.ok) throw new Error("Sign-out could not be verified. Try again.");
  const csrf = await csrfResponse.json();
  if (typeof csrf.csrfToken !== "string") throw new Error("Sign-out could not be verified. Try again.");
  const response = await fetch("/api/auth/signout", { method: "POST", cache: "no-store", redirect: "error",
    signal: AbortSignal.timeout(20000), headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ csrfToken: csrf.csrfToken, callbackUrl: "/login", json: "true" }) });
  if (!response.ok) throw new Error("Secure sign-out is pending. Your cookie was kept until your session is denied. Retry to check the same request.");
}

export function postLoginPath(defaultPath: string) {
  const value = new URLSearchParams(window.location.search).get("returnTo");
  return value && /^\/browser-companion\?pairing=[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value) ? value : defaultPath;
}
