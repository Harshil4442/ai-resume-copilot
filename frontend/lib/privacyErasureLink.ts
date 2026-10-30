/** Fragment proof stays in this document's memory, never in storage or the DOM. */
export const ERASURE_TOKEN = /^[A-Za-z0-9_-]{43}$/;
export type PrivacyErasureOutcome = "submitted" | "complete" | "invalid" | "uncertain";
let pendingToken: string | null = null;
let pendingOutcome: PrivacyErasureOutcome | null = null;
let binding = Symbol();

export function clearPrivacyErasureLink() {
  pendingToken = null;
  pendingOutcome = null;
  binding = Symbol();
}

export function setPrivacyErasureOutcome(expected: symbol, outcome: PrivacyErasureOutcome | null) {
  if (binding !== expected) return false;
  pendingOutcome = outcome;
  // Unknown outcomes retain only this same proof for an explicit human action.
  if (outcome === "complete" || outcome === "invalid") pendingToken = null;
  return true;
}

export function capturePrivacyErasureLink() {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== "/privacy-erasure/confirm") { clearPrivacyErasureLink(); return; }
  const fragment = window.location.hash;
  if (!fragment && !window.location.search) return;
  clearPrivacyErasureLink();
  const queried = Boolean(window.location.search);
  // Strip even malformed proof before instrumentation or form rendering.
  window.history.replaceState(window.history.state, "", window.location.pathname);
  if (queried) return;
  const parameters = new URLSearchParams(fragment.slice(1));
  const token = parameters.get("token");
  if (Array.from(parameters.keys()).join(",") === "token" && token && ERASURE_TOKEN.test(token)) pendingToken = token;
}

export function readPrivacyErasureLink() {
  capturePrivacyErasureLink();
  return { token: pendingToken, outcome: pendingOutcome, binding };
}
