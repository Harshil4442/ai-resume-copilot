"use client";

import { isPasswordResetPath } from "./passwordResetPrivacy";

type EventParams = Record<string, string | number | boolean | null | undefined>;
type PostHogClient = Pick<typeof import("posthog-js").default,
  "capture" | "has_opted_in_capturing" | "opt_out_capturing" | "reset">;

// Public pages can track consented GA events without loading the PostHog SDK.
// AnalyticsConsent registers it only after the visitor has opted in.
let posthogClient: PostHogClient | null = null;

export function setPostHogClient(client: PostHogClient | null) {
  posthogClient = client;
}

export function getPostHogClient(): PostHogClient | null {
  return posthogClient;
}

function analyticsConsentGranted(): boolean {
  try {
    const record = JSON.parse(window.localStorage.getItem("hirewiz_cookie_consent") || "null");
    const savedAt = new Date(record?.savedAt).getTime();
    return record?.version === 2 && record.preference === "analytics" && Number.isFinite(savedAt)
      && Date.now() - savedAt <= 365 * 24 * 60 * 60 * 1000;
  } catch { return false; }
}

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
  }
}

export function trackEvent(eventName: string, params: EventParams = {}) {
  if (typeof window === "undefined" || isPasswordResetPath(window.location.pathname) || !analyticsConsentGranted()) return;
  if (typeof window.gtag === "function") {
    window.gtag("event", eventName, params);
  }
  try {
    if (posthogClient?.has_opted_in_capturing()) {
      posthogClient.capture(eventName, params);
    }
  } catch {
    // Analytics must never interrupt a product action.
  }
}

export function resetAnalyticsIdentity() {
  if (typeof window === "undefined") return;
  try {
    posthogClient?.reset();
  } catch {
    // Analytics cleanup must not prevent sign-out or account deletion.
  }
}
