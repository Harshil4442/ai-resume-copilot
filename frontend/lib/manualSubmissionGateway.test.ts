// @vitest-environment node
import { createHash } from "node:crypto";
import { getToken } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { manualSubmissionGateway } from "./manualSubmissionGateway";
import { POST } from "../app/api/employer-applications/[id]/report-submitted/route";

vi.mock("next-auth/jwt", () => ({ getToken: vi.fn() }));
const origin = "https://hirewiz.example";
const secret = "synthetic-manual-report-nextauth-secret";
const csrf = "a".repeat(64);
const digest = "b".repeat(64);
const bearer = "synthetic-private-account-bearer";
const upstream = vi.fn();
function report() {
  return { id: "app_local", status: "user_reported", application_mode: "manual", charged_credits: 0, reserved_credits: 0, receipt: null,
    candidate_report: { source: "candidate_report", reported_at: "2026-10-14T18:00:00+00:00", saved_package_digest: digest, employer_confirmed: false, used_file_verified: false } };
}
type Options = { origin?: string; csrf?: string | null; cookie?: string; headers?: Record<string, string>; query?: string; body?: string | Uint8Array<ArrayBuffer> };
function request(options: Options = {}) {
  const headers = new Headers({ "Content-Type": "application/json", Origin: options.origin ?? origin, "Sec-Fetch-Site": "same-origin", ...options.headers });
  headers.set("Cookie", options.cookie ?? `__Host-next-auth.csrf-token=${encodeURIComponent(`${csrf}|${createHash("sha256").update(csrf + secret).digest("hex")}`)}`);
  if (options.csrf !== null) headers.set("X-Hirewiz-Account-CSRF", options.csrf ?? csrf);
  return new NextRequest(`${origin}/api/employer-applications/app_local/report-submitted${options.query ?? ""}`,
    { method: "POST", headers, body: options.body ?? JSON.stringify({ package_digest: digest, confirmed: true }) });
}
function privateResponse(response: Response) {
  expect(response.headers.get("Cache-Control")).toBe("private, no-store"); expect(response.headers.get("Pragma")).toBe("no-cache"); expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
}
beforeEach(() => { vi.resetAllMocks(); vi.stubEnv("NEXTAUTH_URL", origin); vi.stubEnv("NEXTAUTH_SECRET", secret); vi.stubEnv("BACKEND_URL", "https://api.example/api/");
  vi.mocked(getToken).mockResolvedValue({ accessToken: bearer, hirewizUserId: 12 }); upstream.mockResolvedValue(Response.json(report())); vi.stubGlobal("fetch", upstream); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("candidate-reported manual submission transport", () => {
  it("uses the dedicated route, genuine same-origin CSRF and server-held bearer to send only the saved digest and explicit confirmation", async () => {
    const response = await POST(request(), { params: Promise.resolve({ id: "app_local" }) }); expect(response.status).toBe(200); expect(await response.json()).toEqual(report()); privateResponse(response);
    expect(upstream).toHaveBeenCalledTimes(1); const [url, init] = upstream.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example/api/v1/employer-jobs/applications/app_local/report-submitted");
    expect(new TextDecoder().decode(init.body as Uint8Array)).toBe(JSON.stringify({ package_digest: digest, confirmed: true }));
    const headers = new Headers(init.headers); expect(headers.get("Authorization")).toBe(`Bearer ${bearer}`); expect(headers.has("Cookie")).toBe(false); expect(headers.has("X-Hirewiz-Account-CSRF")).toBe(false);
    expect(init.method).toBe("POST"); expect(init.credentials).toBe("omit"); expect(init.redirect).toBe("error"); expect(init.cache).toBe("no-store");
  });
  it("projects a zero-fee candidate statement and retained timestamp, excluding arbitrary upstream credentials and employer fields", async () => {
    upstream.mockResolvedValue(Response.json({ ...report(), access_token: "synthetic-secret-reply", employer_response: "unverified claim", candidate_report: { ...report().candidate_report, private_note: "synthetic-hidden-detail" } }));
    const response = await manualSubmissionGateway(request(), "app_local"); const body = await response.json(); expect(body).toEqual(report()); privateResponse(response);
    expect(JSON.stringify(body)).not.toContain("synthetic-secret-reply"); expect(JSON.stringify(body)).not.toContain("synthetic-hidden-detail");
    expect(body.candidate_report.reported_at).toBe("2026-10-14T18:00:00+00:00"); expect(body.candidate_report.employer_confirmed).toBe(false); expect(body.candidate_report.used_file_verified).toBe(false);
  });
  it("rejects an absent signed-in account before any upstream operation", async () => {
    vi.mocked(getToken).mockResolvedValue(null); const response = await manualSubmissionGateway(request(), "app_local"); expect(response.status).toBe(401); privateResponse(response); expect(upstream).not.toHaveBeenCalled();
  });
  it.each<Options>([{ origin: "https://foreign.example" }, { csrf: null }, { csrf: "0".repeat(64) }, { cookie: `__Host-next-auth.csrf-token=${csrf}|${"0".repeat(64)}` },
    { headers: { Authorization: "Bearer synthetic-client-token" } }, { headers: { "Sec-Fetch-Site": "cross-site" } }, { query: "?submit=true" }, { headers: { "Content-Type": "text/plain" } }, { headers: { "Content-Encoding": "gzip" } }])("refuses cross-origin/CSRF/client-bearer or body transport ambiguity before authentication", async (options) => {
    const response = await manualSubmissionGateway(request(options), "app_local"); expect(response.status).toBe(403); privateResponse(response); expect(getToken).not.toHaveBeenCalled(); expect(upstream).not.toHaveBeenCalled();
  });
  it.each(["../other", "app_local/submit", "app_local%2Fsubmit", "", "x".repeat(65)])("rejects an arbitrary application selector %s before forwarding", async (id) => {
    expect((await manualSubmissionGateway(request(), id)).status).toBe(403); expect(upstream).not.toHaveBeenCalled();
  });
  it.each([{}, { confirmed: false, package_digest: digest }, { confirmed: "true", package_digest: digest }, { confirmed: true, package_digest: "invalid" },
    { confirmed: true, package_digest: digest, receipt: "invented" }, { confirmed: true, package_digest: digest, charged_credits: 0 }, []])("requires exactly the saved digest plus boolean true confirmation", async (payload) => {
    const response = await manualSubmissionGateway(request({ body: JSON.stringify(payload) }), "app_local"); expect(response.status).toBe(422); expect(upstream).not.toHaveBeenCalled();
  });
  it.each([
    { ...report(), id: "other_application" }, { ...report(), status: "confirmed" }, { ...report(), application_mode: "api" }, { ...report(), charged_credits: 1 },
    { ...report(), reserved_credits: 1 }, { ...report(), receipt: { confirmation: "invented" } },
    { ...report(), candidate_report: { ...report().candidate_report, saved_package_digest: "c".repeat(64) } },
    { ...report(), candidate_report: { ...report().candidate_report, employer_confirmed: true } },
    { ...report(), candidate_report: { ...report().candidate_report, used_file_verified: true } },
    { ...report(), candidate_report: { ...report().candidate_report, source: "employer_receipt" } },
    { ...report(), candidate_report: { ...report().candidate_report, reported_at: "invalid-date" } },
  ])("refuses provider-confirmed/charged/mismatched assertions instead of relabelling them as a candidate report", async (reply) => {
    upstream.mockResolvedValue(Response.json(reply)); const response = await manualSubmissionGateway(request(), "app_local"); expect(response.status).toBe(503); privateResponse(response); expect(await response.text()).toContain("no automatic retry"); expect(upstream).toHaveBeenCalledTimes(1);
  });
  it("never retries a lost reply and never exposes its raw error detail", async () => {
    upstream.mockRejectedValue(new Error("synthetic-private-network-error")); const response = await manualSubmissionGateway(request(), "app_local"); expect(response.status).toBe(503); privateResponse(response); expect(upstream).toHaveBeenCalledTimes(1); expect(await response.text()).not.toContain("synthetic-private-network-error");
  });
  it.each([409, 429, 503])("retains the safe %s refusal without another report POST", async (status) => {
    upstream.mockResolvedValue(Response.json({ detail: "synthetic-private-refusal" }, { status })); const response = await manualSubmissionGateway(request(), "app_local"); expect(response.status).toBe(status); privateResponse(response); expect(upstream).toHaveBeenCalledTimes(1); expect(await response.text()).not.toContain("synthetic-private-refusal");
  });
  it.each(["request", "response"])("bounds %s JSON bytes without projecting a partial report", async (side) => {
    if (side === "response") upstream.mockResolvedValue(Response.json({ ...report(), padding: "x".repeat(4096) }));
    const response = await manualSubmissionGateway(request(side === "request" ? { body: JSON.stringify({ padding: "x".repeat(4096) }) } : {}), "app_local");
    expect(response.status).toBe(503); expect(upstream).toHaveBeenCalledTimes(side === "request" ? 0 : 1);
  });
});
