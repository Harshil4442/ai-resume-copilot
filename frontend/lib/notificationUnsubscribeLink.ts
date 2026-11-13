/** This document's one-shot proof custody. No DOM, storage, query keys or telemetry. */
let token: string | null = null;
let outcome: "submitted" | "complete" | "invalid" | "uncertain" | null = null;
let binding = Symbol();

export function clearNotificationUnsubscribeLink() {
  token = null; outcome = null; binding = Symbol();
}

export function captureNotificationUnsubscribeLink() {
  if (typeof window === "undefined") return;
  if (window.location.pathname.replace(/\/$/, "") !== "/notifications/unsubscribe") {
    clearNotificationUnsubscribeLink(); return;
  }
  const fragment = window.location.hash;
  if (!fragment && !window.location.search) return;
  clearNotificationUnsubscribeLink();
  const queried = Boolean(window.location.search);
  window.history.replaceState(window.history.state, "", window.location.pathname);
  if (queried || fragment.length > 4200) return;
  const parameters = new URLSearchParams(fragment.slice(1));
  const value = parameters.get("token");
  if (Array.from(parameters.keys()).join(",") === "token" && value && value.length <= 4096
    && /^hwdu1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/.test(value)) token = value;
}

export function readNotificationUnsubscribeLink() {
  captureNotificationUnsubscribeLink();
  return { token, outcome, binding };
}

export function setNotificationUnsubscribeOutcome(expected: symbol, next: NonNullable<typeof outcome>) {
  if (binding !== expected) return false;
  token = null; outcome = next;
  return true;
}
