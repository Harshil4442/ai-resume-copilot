"use client";

import { useReportWebVitals } from "next/web-vitals";

type Vital = { name: string; value: number; id: string; rating: string };

// Keep identifiers, query strings and candidate content out of performance events.
export function vitalProperties(metric: Vital, pathname: string, width: number, connection?: string) {
  if (!["LCP", "INP", "CLS"].includes(metric.name) || !Number.isFinite(metric.value) || metric.value < 0) return null;
  const section = pathname.split("/")[1] || "home";
  const route = ["home", "dashboard", "workspace", "resume", "employer-jobs", "billing", "market", "profile", "pricing", "login", "register"].includes(section) ? section : "other";
  return {
    metric_name: metric.name, metric_value: metric.value, metric_id: metric.id,
    metric_rating: ["good", "needs-improvement", "poor"].includes(metric.rating) ? metric.rating : "unknown",
    route_group: route, device_group: width < 768 ? "mobile" : width < 1024 ? "tablet" : width < 1440 ? "laptop" : "desktop",
    connection_group: ["slow-2g", "2g", "3g", "4g"].includes(connection || "") ? connection : "unknown",
    non_interaction: true,
  };
}

function consentStillGranted() {
  try {
    const consent = JSON.parse(window.localStorage.getItem("hirewiz_cookie_consent") || "null");
    const savedAt = new Date(consent?.savedAt).getTime();
    return consent?.version === 2 && consent.preference === "analytics" && Number.isFinite(savedAt)
      && Date.now() - savedAt <= 365 * 24 * 60 * 60 * 1000;
  } catch { return false; }
}

function report(metric: Vital) {
  if (!consentStillGranted() || !window.gtag) return;
  const connection = (navigator as Navigator & { connection?: { effectiveType?: string } }).connection?.effectiveType;
  const properties = vitalProperties(metric, window.location.pathname, window.innerWidth, connection);
  if (properties) window.gtag("event", "web_vital", {
    ...properties,
    page_location: window.location.origin + "/" + properties.route_group,
    page_referrer: "", page_title: "HireWiz performance",
  });
}

export default function CoreWebVitals() {
  useReportWebVitals(report);
  return null;
}
