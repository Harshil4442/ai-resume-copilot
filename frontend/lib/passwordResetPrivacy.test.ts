import { afterEach, describe, expect, it, vi } from "vitest";
import { isPasswordResetPath, isPasswordResetUrl, resetTelemetry } from "./passwordResetPrivacy";

const sentry = vi.hoisted(() => ({ init: vi.fn(), captureRouterTransitionStart: vi.fn() }));
vi.mock("@sentry/nextjs", () => sentry);
afterEach(() => { history.replaceState(null, "", "/"); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("reset telemetry exclusion before hydration", () => {
  it.each(["/forgot-password", "/reset-password/", "/account-recovery", "/api/password-reset/request", "/api/auth/password-reset/confirm"])("excludes exact sensitive surface %s", (path) => {
    expect(isPasswordResetPath(path)).toBe(true);
    expect(isPasswordResetUrl(`https://www.example.invalid${path}#token=private`)).toBe(true);
    expect(isPasswordResetUrl(`POST ${path}`)).toBe(true);
  });
  it("retains unrelated telemetry but drops reset URL and navigation breadcrumbs", () => {
    expect(resetTelemetry({ request: { url: "https://www.example.invalid/dashboard" } })).toBe(false);
    expect(resetTelemetry({ request: { url: "https://www.example.invalid/api/password-reset/confirm" } })).toBe(true);
    expect(resetTelemetry({ transaction: "POST /api/auth/password-reset/request" })).toBe(true);
    expect(resetTelemetry({ breadcrumbs: [{ data: { from: "/reset-password#token=private", to: "/login" } }] })).toBe(true);
    expect(isPasswordResetPath("/reset-password-example")).toBe(false);
  });
  it("strips link proof before Sentry initialization and suppresses reset navigation hooks and events", async () => {
    vi.resetModules(); sentry.init.mockReset(); sentry.captureRouterTransitionStart.mockReset();
    const token = "T".repeat(43);
    history.replaceState(null, "", `/reset-password#token=${token}`);
    sentry.init.mockImplementationOnce(() => { expect(location.hash).toBe(""); });
    vi.stubEnv("NEXT_PUBLIC_SENTRY_DSN", "https://synthetic.invalid/123");
    vi.stubEnv("NEXT_PUBLIC_GA_MEASUREMENT_ID", "G-SYNTHETIC");
    const instrumentation = await import("../instrumentation-client");
    expect(sentry.init).toHaveBeenCalledOnce();
    const options = sentry.init.mock.calls[0][0] as { enabled: boolean; beforeSend: (value: object) => object | null; beforeBreadcrumb: (value: object) => object | null };
    expect(options.enabled).toBe(false);
    expect((window as unknown as Record<string, unknown>)["ga-disable-G-SYNTHETIC"]).toBe(true);
    expect(options.beforeSend({ message: "private" })).toBeNull();
    expect(options.beforeBreadcrumb({ data: { url: location.href } })).toBeNull();
    const { takePasswordResetToken } = await import("./passwordResetLink");
    expect(takePasswordResetToken()).toBe(token);
    history.replaceState(null, "", "/dashboard");
    (window as unknown as Record<string, unknown>)["ga-disable-G-SYNTHETIC"] = false;
    instrumentation.onRouterTransitionStart("/reset-password#token=private", "push");
    expect((window as unknown as Record<string, unknown>)["ga-disable-G-SYNTHETIC"]).toBe(true);
    expect(sentry.captureRouterTransitionStart).not.toHaveBeenCalled();
    instrumentation.onRouterTransitionStart("/jobs", "push");
    expect(sentry.captureRouterTransitionStart).toHaveBeenCalledOnce();
  });
  it("declares restrictive HTTP reset policies and leaves ordinary auth CSP intact", async () => {
    const modulePath = "../next.config.mjs";
    const { default: config } = await import(modulePath);
    const all = await config.headers();
    for (const source of ["/forgot-password", "/reset-password", "/account-recovery", "/api/password-reset/:path*"]) {
      const rule = all.find((row: { source: string }) => row.source === source);
      const headers = Object.fromEntries(rule.headers.map((row: { key: string; value: string }) => [row.key, row.value]));
      expect(headers["Referrer-Policy"]).toBe("no-referrer");
      expect(headers["Cache-Control"]).toContain("no-store"); expect(headers["X-Robots-Tag"]).toContain("noindex");
      expect(headers["Content-Security-Policy"]).toContain("connect-src 'self'");
      expect(headers["Content-Security-Policy"]).toContain("frame-src 'none'");
      expect(headers["Content-Security-Policy"]).not.toMatch(/google|sentry|posthog/);
    }
    expect(all[0].headers.find((row: { key: string }) => row.key === "Content-Security-Policy").value).toContain("https://accounts.google.com/gsi/client");
  });
});
