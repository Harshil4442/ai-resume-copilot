/** Captured before instrumentation starts; never persisted or rendered. */
let pendingToken: string | null = null;
export const RESET_TOKEN = /^[A-Za-z0-9_-]{43}$/;

export function clearPasswordResetToken(expected?: string | null) {
  if (expected === undefined || pendingToken === expected) pendingToken = null;
}

export function capturePasswordResetLink() {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== "/reset-password") { clearPasswordResetToken(); return; }
  const fragment = window.location.hash;
  if (!fragment) return;
  pendingToken = null;
  // Remove even malformed fragments before telemetry or form rendering.
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
  const parameters = new URLSearchParams(fragment.slice(1));
  const token = parameters.get("token");
  if (Array.from(parameters.keys()).join(",") === "token" && token && RESET_TOKEN.test(token)) pendingToken = token;
}

export function readPasswordResetToken() {
  capturePasswordResetLink();
  return pendingToken;
}
