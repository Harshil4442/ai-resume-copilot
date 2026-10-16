import React from "react";
import { render } from "@testing-library/react";
import { useReportWebVitals } from "next/web-vitals";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/web-vitals", () => ({ useReportWebVitals: vi.fn() }));
import CoreWebVitals, { vitalProperties } from "./CoreWebVitals";

describe("performance data boundary", () => {
  afterEach(() => { localStorage.clear(); delete window.gtag; });
  it("sends no metric before consent or after consent is withdrawn", () => {
    window.gtag = vi.fn();
    render(<CoreWebVitals />);
    const report = vi.mocked(useReportWebVitals).mock.calls.at(-1)![0];
    const metric = { name: "LCP", value: 1400, id: "page-sample", rating: "good", delta: 1400, entries: [], navigationType: "navigate" } as const;
    report(metric as Parameters<typeof report>[0]);
    expect(window.gtag).not.toHaveBeenCalled();
    localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "analytics", savedAt: new Date().toISOString() }));
    report(metric as Parameters<typeof report>[0]);
    expect(window.gtag).toHaveBeenCalledOnce();
    localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "essential", savedAt: new Date().toISOString() }));
    report(metric as Parameters<typeof report>[0]);
    expect(window.gtag).toHaveBeenCalledOnce();
  });
  it("reports a static section instead of a private opportunity ID or query", () => {
    const properties = vitalProperties({ name: "LCP", value: 1400, id: "page-sample", rating: "good" }, "/workspace/private-candidate-job?resume=secret", 390, "4g");
    expect(properties).toMatchObject({ route_group: "workspace", device_group: "mobile", metric_value: 1400 });
    expect(JSON.stringify(properties)).not.toMatch(/private-candidate|secret|resume/);
  });
  it("bounds metric and metadata values without recording arbitrary paths", () => {
    expect(vitalProperties({ name: "unknown-private-event", value: 1, id: "sample", rating: "good" }, "/secret", 1440)).toBeNull();
    expect(vitalProperties({ name: "CLS", value: Number.NaN, id: "sample", rating: "good" }, "/secret", 1440)).toBeNull();
    expect(vitalProperties({ name: "INP", value: 100, id: "sample", rating: "arbitrary", }, "/private-email@host", 1440, "arbitrary")).toMatchObject({ route_group: "other", connection_group: "unknown", metric_rating: "unknown" });
  });
});
