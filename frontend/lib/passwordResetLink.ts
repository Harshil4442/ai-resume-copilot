/** Captured before instrumentation starts; never persisted or rendered. */
let pendingToken: string | null = null;
export type PasswordResetOutcome = "submitted" | "complete" | "invalid" | "uncertain";
let pendingOutcome: PasswordResetOutcome | null = null;
let binding = Symbol();
export const RESET_TOKEN = /^[A-Za-z0-9_-]{43}$/;

export function clearPasswordResetToken(expected?: string | null) {
  if (expected === undefined) {
    pendingToken = null; pendingOutcome = null; binding = Symbol();
  } else if (pendingToken === expected) pendingToken = null;
}

export function setPasswordResetOutcome(expectedBinding: symbol, outcome: PasswordResetOutcome) {
  if (binding !== expectedBinding) return false;
  pendingToken = null;
  pendingOutcome = outcome;
  return true;
}

export function capturePasswordResetLink() {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== "/reset-password") { clearPasswordResetToken(); return; }
  const fragment = window.location.hash;
  if (!fragment) return;
  clearPasswordResetToken();
  // Remove even malformed fragments before telemetry or form rendering.
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
  const parameters = new URLSearchParams(fragment.slice(1));
  const token = parameters.get("token");
  if (Array.from(parameters.keys()).join(",") === "token" && token && RESET_TOKEN.test(token)) pendingToken = token;
}

export function readPasswordResetLink() {
  capturePasswordResetLink();
  return { token: pendingToken, outcome: pendingOutcome, binding };
}

export function readPasswordResetToken() {
  return readPasswordResetLink().token;
}
