"use client";

import posthog from "posthog-js";

type EventParams = Record<string, string | number | boolean | null | undefined>;

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
  }
}

export function trackEvent(eventName: string, params: EventParams = {}) {
  if (typeof window === "undefined") return;
  if (typeof window.gtag === "function") {
    window.gtag("event", eventName, params);
  }
  try {
    if (posthog.has_opted_in_capturing()) {
      posthog.capture(eventName, params);
    }
  } catch {
    // Analytics must never interrupt a product action.
  }
}

export function resetAnalyticsIdentity() {
  if (typeof window === "undefined") return;
  try {
    posthog.reset();
  } catch {
    // Analytics cleanup must not prevent sign-out or account deletion.
  }
}
