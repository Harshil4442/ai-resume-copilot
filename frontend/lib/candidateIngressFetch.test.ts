// @vitest-environment node
import { beforeEach, expect, it, vi } from "vitest";

const base = "https://api.example.invalid";
const path = "/api/auth/candidate/v1/login";
const availability = "/api/auth/candidate/v1/availability";
let send: typeof import("./candidateIngressFetch").candidateIngressFetch;
beforeEach(async () => {
  vi.restoreAllMocks(); vi.resetModules();
  vi.stubEnv("NEXTAUTH_URL", "https://candidate.example.invalid");
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", "6e".repeat(32));
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "fixture_clock_v1");
  send = (await import("./candidateIngressFetch")).candidateIngressFetch;
});
function claim(init: RequestInit) {
  const header = new Headers(init.headers).get("x-hirewiz-candidate-ingress")!;
  return JSON.parse(Buffer.from(header.split(".")[0], "base64url").toString());
}
function unavailable(lower: number, upper = lower + 5) {
  return Response.json({ detail: "Private candidate transport is unavailable" }, { status: 503,
    headers: { "x-hirewiz-ingress-time-lower": String(lower), "x-hirewiz-ingress-time-upper": String(upper) } });
}
const ready = () => Response.json({ fresh_registration: true, legacy_enrollment: true });

it("aligns a skewed caller with the provider clock using only read-only probes", async () => {
  const provider = Date.now() - 15_000;
  const body = JSON.stringify({ email: "synthetic@example.invalid", password: "private-synthetic-input" });
  const calls: { url: string; init: RequestInit; assertion: ReturnType<typeof claim> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init, assertion: claim(init) });
    if (calls.length === 1) return unavailable(provider);
    return url.endsWith(availability) ? ready() : Response.json({ status: "DONE" });
  }));
  expect((await send(base, path, { method: "POST", body, headers: { authorization: "Bearer synthetic-private-token" } })).status).toBe(200);
  expect(calls.map(c => [c.url, c.init.method])).toEqual([
    [base + availability, "GET"], [base + availability, "GET"], [base + path, "POST"],
  ]);
  for (const c of calls.slice(0, 2)) {
    expect(c.init.body).toBeUndefined();
    expect(new Headers(c.init.headers).has("authorization")).toBe(false);
  }
  expect(calls[2].init.body).toBe(body);
  expect(new Headers(calls[2].init.headers).get("authorization")).toBe("Bearer synthetic-private-token");
  expect(Math.abs(calls[2].assertion.issued_at_ms - provider)).toBeLessThan(1000);
  expect(new Set(calls.map(c => c.assertion.request_id)).size).toBe(3);
  for (const c of calls) expect(c.assertion.expires_at_ms - c.assertion.issued_at_ms).toBe(5000);
});

it("never repeats a credential POST, including an unknown or rejected outcome", async () => {
  const fetch = vi.fn(async (url: string) => url.endsWith(availability) ? ready() : unavailable(Date.now()));
  vi.stubGlobal("fetch", fetch);
  expect((await send(base, path, { method: "POST", body: "{}" })).status).toBe(503);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith(path))).toHaveLength(1);
});

it.each([[-1, 5], [100, 99], [100, 602], [Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER]])(
  "rejects malformed or unbounded clock hints before sending a credential body: %s %s", async (low, high) => {
    const fetch = vi.fn(async () => unavailable(low, high)); vi.stubGlobal("fetch", fetch);
    expect((await send(base, path, { method: "POST", body: "private-synthetic-input" })).status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]).toHaveLength(2);
  },
);

it("keeps the accepted estimate process-local and advances it with monotonic time", async () => {
  const monotonic = vi.spyOn(performance, "now").mockReturnValue(1000);
  const fetch = vi.fn<typeof globalThis.fetch>(async () => ready()); vi.stubGlobal("fetch", fetch);
  await send(base, path, { method: "POST", body: "{}" });
  const first = claim(fetch.mock.calls[1][1] as RequestInit).issued_at_ms;
  vi.spyOn(Date, "now").mockReturnValue(Date.now() + 600_000);
  monotonic.mockReturnValue(2000);
  await send(base, path, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(claim(fetch.mock.calls[2][1] as RequestInit).issued_at_ms).toBe(first + 1000);
  await send("https://other-api.example.invalid", path, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(5); // Other backends never reuse this estimate.
});

it("preserves unenrolled legacy authentication when native provisioning is unavailable", async () => {
  const fetch = vi.fn(async (url: string) => url.endsWith(availability) ? new Response(null, { status: 503 }) : Response.json({ id: 1 }));
  vi.stubGlobal("fetch", fetch);
  expect((await send(base, "/api/auth/login", { method: "POST", body: "{}" })).status).toBe(200);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("keeps legacy auth without transport provision on its original single request", async () => {
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", ""); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "");
  const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ id: 1 })); vi.stubGlobal("fetch", fetch);
  await send(base, "/api/auth/login", { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(new Headers((fetch.mock.calls[0][1] as RequestInit).headers).has("x-hirewiz-candidate-ingress")).toBe(false);
});
