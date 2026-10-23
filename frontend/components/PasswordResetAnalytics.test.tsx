import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AnalyticsConsent from "./AnalyticsConsent";
import { trackEvent } from "../lib/analytics";

const state = vi.hoisted(() => ({ path: "/" }));
const posthog = vi.hoisted(() => ({ init: vi.fn(), capture: vi.fn(), opt_out_capturing: vi.fn(), opt_in_capturing: vi.fn(),
  identify: vi.fn(), reset: vi.fn(), has_opted_in_capturing: vi.fn(() => true) }));
vi.mock("posthog-js", () => ({ default: posthog }));
vi.mock("next/navigation", () => ({ usePathname: () => state.path }));
vi.mock("next-auth/react", () => ({ useSession: () => ({ status: "unauthenticated", data: null }) }));
vi.mock("next/script", () => ({ default: (props: { src?: string }) => <span data-testid="third-party-script">{props.src}</span> }));
vi.mock("./CoreWebVitals", () => ({ default: () => <span data-testid="web-vitals" /> }));
const props = { gaMeasurementId: "G-SYNTHETIC", posthogKey: "synthetic-key", posthogHost: "https://telemetry.invalid" };
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  localStorage.setItem("hirewiz_cookie_consent", JSON.stringify({ version: 2, preference: "analytics", savedAt: new Date().toISOString() }));
  window.gtag = vi.fn();
});
afterEach(() => { cleanup(); localStorage.clear(); delete window.gtag; history.replaceState(null, "", "/"); });

describe("reset surfaces never activate consented analytics", () => {
  it.each(["/forgot-password", "/reset-password", "/account-recovery"])("keeps %s free of analytics scripts, pageviews and form events despite saved consent", (path) => {
    state.path = path; history.replaceState(null, "", path);
    const view = render(<AnalyticsConsent {...props} />);
    expect(view.container).toBeEmptyDOMElement();
    expect(posthog.init).not.toHaveBeenCalled(); expect(posthog.capture).not.toHaveBeenCalled();
    expect(posthog.opt_out_capturing).toHaveBeenCalled();
    expect((window as unknown as Record<string, unknown>)["ga-disable-G-SYNTHETIC"]).toBe(true);
    expect(window.gtag).toHaveBeenCalledWith("consent", "update", { analytics_storage: "denied" });
    vi.mocked(window.gtag!).mockClear();
    trackEvent("sensitive-form-event", { value: "not-sent" });
    expect(window.gtag).not.toHaveBeenCalled(); expect(posthog.capture).not.toHaveBeenCalled();
  });
  it("removes existing analytics components on navigation to reset while ordinary consent still works", async () => {
    state.path = "/dashboard"; history.replaceState(null, "", state.path);
    const view = render(<AnalyticsConsent {...props} />);
    await waitFor(() => expect(posthog.capture).toHaveBeenCalledWith("$pageview", expect.objectContaining({ path: "/dashboard" })));
    expect(view.getAllByTestId("third-party-script")).toHaveLength(2);
    const options = posthog.init.mock.calls[0][1] as { before_send: (event: { properties: Record<string, unknown> }) => unknown };
    expect(options.before_send({ properties: { $current_url: location.href } })).not.toBeNull();
    expect(options.before_send({ properties: { $current_url: "https://example.invalid/reset-password#token=private" } })).toBeNull();
    posthog.capture.mockClear(); posthog.opt_out_capturing.mockClear();
    state.path = "/reset-password"; history.replaceState(null, "", state.path);
    view.rerender(<AnalyticsConsent {...props} />);
    expect(view.queryByTestId("third-party-script")).toBeNull();
    expect(view.queryByTestId("web-vitals")).toBeNull();
    expect(posthog.capture).not.toHaveBeenCalled(); expect(posthog.opt_out_capturing).toHaveBeenCalled();
  });
});
