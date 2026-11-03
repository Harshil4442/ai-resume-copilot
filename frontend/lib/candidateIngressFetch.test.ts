// @vitest-environment node
import { beforeEach, expect, it, vi } from "vitest";
import { candidateIngressHeaders } from "./candidateIngressServer";

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

it("fails closed before provisioned sign-in when its non-authorizing health hint is unavailable", async () => {
  const fetch = vi.fn(async () => new Response(null, { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  await expect(send(base, "/api/auth/login", { method: "POST", body: "{}" })).rejects.toThrow(/^AUTH_UNAVAILABLE$/);
  expect(fetch).toHaveBeenCalledExactlyOnceWith(base + "/api/health", expect.objectContaining({ method: "GET" }));
});

it("keeps legacy auth without transport provision on its original single request", async () => {
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", ""); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "");
  const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ id: 1 })); vi.stubGlobal("fetch", fetch);
  await send(base, "/api/auth/login", { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(new Headers((fetch.mock.calls[0][1] as RequestInit).headers).has("x-hirewiz-candidate-ingress")).toBe(false);
});

const healthDate = "Wed, 04 Nov 2026 10:00:00 GMT";
function healthHint(init: RequestInit, headers: Record<string, string> = {}, status = 200) {
  return new Response(null, { status, headers: {
    date: healthDate, "x-correlation-id": new Headers(init.headers).get("x-correlation-id")!, ...headers,
  } });
}

it.each(["login", "google-login"])("bootstraps %s with an unsigned fresh health GET and sends exactly one unchanged signed credential POST", async (operation) => {
  const endpoint = `/api/auth/${operation}`;
  const body = JSON.stringify({ email: "synthetic@example.invalid", password: "private-synthetic-input", id_token: "synthetic-google-proof" });
  const controller = new AbortController();
  const init = { method: "POST", body, signal: controller.signal,
    headers: { authorization: "Bearer synthetic-private-token", cookie: "private-cookie", "content-type": "application/json" } };
  vi.spyOn(Date, "now").mockReturnValue(Date.parse(healthDate) + 15_000);
  const fetch = vi.fn<typeof globalThis.fetch>(async (url, request) => String(url).endsWith("/api/health")
    ? healthHint(request!, { age: "0" }) : Response.json({ status: "DONE" }));
  vi.stubGlobal("fetch", fetch);
  expect((await send(base, endpoint, init)).status).toBe(200);
  expect(fetch.mock.calls.map(([url, request]) => [url, request?.method])).toEqual([
    [base + "/api/health", "GET"], [base + endpoint, "POST"],
  ]);
  const probe = fetch.mock.calls[0][1]!;
  expect(probe).toMatchObject({ credentials: "omit", redirect: "error", cache: "no-store", signal: controller.signal });
  expect(probe.body).toBeUndefined();
  const probeHeaders = new Headers(probe.headers);
  expect(probeHeaders.get("x-correlation-id")).toMatch(/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/);
  expect(probeHeaders.get("cache-control")).toBe("no-cache, no-store");
  expect(probeHeaders.get("pragma")).toBe("no-cache");
  for (const key of ["authorization", "cookie", "x-hirewiz-candidate-ingress", "content-type"]) expect(probeHeaders.has(key)).toBe(false);
  expect(JSON.stringify(probe)).not.toContain("private-synthetic-input");
  const post = fetch.mock.calls[1][1]!;
  const assertion = claim(post);
  expect(assertion.issued_at_ms).toBeGreaterThanOrEqual(Date.parse(healthDate));
  expect(assertion.issued_at_ms).toBeLessThan(Date.parse(healthDate) + 1000);
  expect(assertion.expires_at_ms - assertion.issued_at_ms).toBe(5000);
  expect(post.body).toBe(body);
  expect(post.signal).toBe(controller.signal);
  expect(new Headers(post.headers).get("authorization")).toBe("Bearer synthetic-private-token");
  expect(new Headers(post.headers).get("x-hirewiz-candidate-ingress")).toBe(
    candidateIngressHeaders(endpoint, init, assertion.issued_at_ms, assertion.request_id).get("x-hirewiz-candidate-ingress"),
  );
});

const invalidHealthHeaders: Record<string, string>[] = [
  { date: "" }, { date: "invalid" }, { date: "2026-11-04T10:00:00Z" },
  { date: "Thu, 04 Nov 2026 10:00:00 GMT" },
  { date: `${healthDate}, ${healthDate}` },
  { "x-correlation-id": "" }, { "x-correlation-id": "replayed-response-nonce" },
  { age: "1" }, { age: "00" }, { age: "-1" }, { age: "0, 0" },
];
it.each(invalidHealthHeaders)("rejects malformed, stale or unbound health hints before any credential POST: %j", async (headers) => {
  const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) => healthHint(init!, headers));
  vi.stubGlobal("fetch", fetch);
  await expect(send(base, "/api/auth/login", { method: "POST", body: "private-synthetic-input" })).rejects.toThrow(/^AUTH_UNAVAILABLE$/);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe(base + "/api/health");
});

it.each([[1000, 1501], [1000, 999], [1000, Infinity], [NaN, 1000]])(
  "rejects an unbounded or inconsistent health RTT before credential POST: %s to %s", async (start, end) => {
    vi.spyOn(performance, "now").mockReturnValueOnce(start).mockReturnValueOnce(end);
    const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) => healthHint(init!)); vi.stubGlobal("fetch", fetch);
    await expect(send(base, "/api/auth/login", { method: "POST", body: "{}" })).rejects.toThrow(/^AUTH_UNAVAILABLE$/);
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

it("keeps a health hint scoped and monotonic for at most the original 30 seconds", async () => {
  const monotonic = vi.spyOn(performance, "now").mockReturnValue(1000);
  const fetch = vi.fn<typeof globalThis.fetch>(async (url, init) => String(url).endsWith("/api/health") ? healthHint(init!) : ready());
  vi.stubGlobal("fetch", fetch);
  const endpoint = "/api/auth/login";
  await send(base, endpoint, { method: "POST", body: "{}" });
  monotonic.mockReturnValue(2000);
  vi.spyOn(Date, "now").mockReturnValue(Date.parse(healthDate) + 600_000);
  await send(base, endpoint, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(claim(fetch.mock.calls[2][1]!).issued_at_ms).toBe(Date.parse(healthDate) + 1000);
  monotonic.mockReturnValue(31_001);
  await send(base, endpoint, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(5);
  await send("https://other-api.example.invalid", endpoint, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(7);
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "another_fixture_key");
  await send(base, endpoint, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(9);
  vi.stubEnv("NEXTAUTH_URL", "https://other-website.example.invalid");
  await send(base, endpoint, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(11);
  const probes = fetch.mock.calls.filter(([url]) => String(url).endsWith("/api/health"));
  expect(new Set(probes.map(([, init]) => new Headers(init!.headers).get("x-correlation-id"))).size).toBe(probes.length);
});

it("does not use the health hint for explicit native availability or other private operations", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(async (url, init) => String(url).endsWith("/api/health") ? healthHint(init!) : ready());
  vi.stubGlobal("fetch", fetch);
  await send(base, "/api/auth/login", { method: "POST", body: "{}" });
  await send(base, "/api/auth/candidate/v1/session");
  await send(base, availability);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    base + "/api/health", base + "/api/auth/login", base + availability,
    base + "/api/auth/candidate/v1/session", base + availability,
  ]);
  expect(new Headers(fetch.mock.calls[2][1]!.headers).has("x-hirewiz-candidate-ingress")).toBe(true);
});

it("returns a backend clock-skew refusal unchanged without retrying the POST or adopting its hints", async () => {
  const refusal = unavailable(Date.parse(healthDate) - 15_000);
  const fetch = vi.fn<typeof globalThis.fetch>(async (url, init) => String(url).endsWith("/api/health") ? healthHint(init!) : refusal);
  vi.stubGlobal("fetch", fetch);
  const endpoint = "/api/auth/login";
  expect(await send(base, endpoint, { method: "POST", body: "{}" })).toBe(refusal);
  expect(fetch).toHaveBeenCalledTimes(2);
  // Another POST occurs only when the caller explicitly invokes sign-in again.
  await send(base, endpoint, { method: "POST", body: "{}" });
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(claim(fetch.mock.calls[2][1]!).issued_at_ms).toBeGreaterThanOrEqual(Date.parse(healthDate));
});

it("requires HTTPS for the implicit sign-in health hint", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(send("http://localhost:8000", "/api/auth/login", { method: "POST", body: "{}" })).rejects.toThrow(/^AUTH_UNAVAILABLE$/);
  expect(fetch).not.toHaveBeenCalled();
});
