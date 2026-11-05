/** Server-only clock estimates; every request still needs fresh backend admission. */
import { candidateIngressHeaders } from "./candidateIngressServer";
import { randomUUID } from "node:crypto";

const AVAILABILITY = "/api/auth/candidate/v1/availability";
const SESSION = "/api/auth/candidate/v1/session";
const HEALTH = "/api/health";
const SIGN_IN = new Set(["/api/auth/login", "/api/auth/google-login"]);
const CLOCK_ESTIMATE_AGE_MS = 30_000;
const MAX_HEALTH_RTT_MS = 500;
type ClockEstimate = { scope: string; issued: number; started: number };
let clock: ClockEstimate | undefined;
let healthClock: ClockEstimate | undefined;

function estimatedTime(scope: string, estimate = clock) {
  if (!estimate || estimate.scope !== scope) return undefined;
  const elapsed = performance.now() - estimate.started;
  if (elapsed < 0 || elapsed > CLOCK_ESTIMATE_AGE_MS) return undefined;
  return estimate.issued + Math.floor(elapsed);
}

function synchronizationTime(response: Response) {
  if (response.status !== 503) return undefined;
  const low = response.headers.get("x-hirewiz-ingress-time-lower");
  const high = response.headers.get("x-hirewiz-ingress-time-upper");
  if (!low || !high || !/^[1-9][0-9]{0,15}$/.test(low) || !/^[1-9][0-9]{0,15}$/.test(high)) return undefined;
  const lower = Number(low), upper = Number(high);
  if (!Number.isSafeInteger(lower) || !Number.isSafeInteger(upper) || lower > Number.MAX_SAFE_INTEGER - 5000
      || upper < lower || upper - lower > 501) return undefined;
  return lower;
}

export async function candidateIngressFetch(base: string, path: string, init: RequestInit = {}) {
  // Validate/bind the original input before any network call. Unprovisioned
  // ordinary legacy auth and unrelated account routes keep their old path.
  const initial = candidateIngressHeaders(path, init);
  const send = (endpoint: string, request: RequestInit, now?: number) => fetch(`${base}${endpoint}`, {
    ...request, headers: candidateIngressHeaders(endpoint, request, now), redirect: "error", cache: "no-store",
  });
  if (!initial.has("x-hirewiz-candidate-ingress")) return send(path, init);
  const target = new URL(base);
  if (target.username || target.password || target.search || target.hash || target.pathname !== "/"
      || (target.protocol !== "https:" && !(process.env.NODE_ENV !== "production"
        && target.protocol === "http:" && ["localhost", "127.0.0.1"].includes(target.hostname)))) throw new Error("AUTH_UNAVAILABLE");
  const scope = `${target.origin}|${process.env.CANDIDATE_AUTH_TRANSPORT_KEY_ID}|${process.env.NEXTAUTH_URL}`;

  async function healthTimeHint(): Promise<ClockEstimate> {
    if (target.protocol !== "https:") throw new Error("AUTH_UNAVAILABLE");
    const nonce = randomUUID();
    const started = performance.now();
    const response = await fetch(`${target.origin}${HEALTH}`, {
      method: "GET", signal: init.signal, credentials: "omit", cache: "no-store", redirect: "error",
      headers: { "X-Correlation-ID": nonce, "Cache-Control": "no-cache, no-store", Pragma: "no-cache" },
    });
    const completed = performance.now();
    try {
      const date = response.headers.get("date");
      const age = response.headers.get("age");
      const issued = date ? Date.parse(date) : NaN;
      if (response.status !== 200 || response.headers.get("x-correlation-id") !== nonce
          || (age !== null && age !== "0") || !date || !Number.isSafeInteger(issued) || issued <= 0
          || issued > Number.MAX_SAFE_INTEGER - CLOCK_ESTIMATE_AGE_MS - 5000
          || new Date(issued).toUTCString() !== date || !Number.isFinite(completed - started)
          || completed < started || completed - started > MAX_HEALTH_RTT_MS) throw new Error("AUTH_UNAVAILABLE");
      // Date has whole-second precision and is only a timestamp hint. Anchor at
      // receipt without rounding forward; the backend still decides freshness
      // from independent provider UTC and performs every native admission check.
      return { scope, issued, started: completed };
    } finally { await response.body?.cancel(); }
  }

  async function availability(request: RequestInit) {
    let issued = estimatedTime(scope) ?? Date.now();
    let started = performance.now();
    let response = await send(AVAILABILITY, request, issued);
    const corrected = synchronizationTime(response);
    if (corrected !== undefined) {
      // Only this read-only probe may repeat, with a new nonce. Credential,
      // enrollment, logout and deletion POSTs are never automatically retried.
      await response.body?.cancel();
      issued = corrected;
      started = performance.now();
      response = await send(AVAILABILITY, request, issued);
    }
    if (response.status === 200) clock = { scope, issued, started };
    else if (clock?.scope === scope) clock = undefined;
    return response;
  }

  if (path === AVAILABILITY && (init.method || "GET") === "GET") return availability(init);
  const method = init.method || "GET";
  if ((method === "POST" && SIGN_IN.has(path)) || (method === "GET" && path === SESSION)) {
    if (init.signal?.aborted) throw new Error("AUTH_UNAVAILABLE");
    let sampled: ClockEstimate | undefined;
    let issued = estimatedTime(scope) ?? estimatedTime(scope, healthClock);
    if (issued === undefined) {
      sampled = await healthTimeHint();
      issued = estimatedTime(scope, sampled);
    }
    if (issued === undefined || init.signal?.aborted
        || scope !== `${target.origin}|${process.env.CANDIDATE_AUTH_TRANSPORT_KEY_ID}|${process.env.NEXTAUTH_URL}`) throw new Error("AUTH_UNAVAILABLE");
    if (sampled) healthClock = sampled;
    // A timestamp hint never replaces native admission. Neither these exact
    // requests nor an unavailable hint fall back or retry the target request.
    return send(path, init, issued);
  }
  if (estimatedTime(scope) === undefined) {
    try {
      // No credential body or bearer is sent to the clock probe.
      const probe = await availability({ method: "GET", signal: init.signal });
      if (!probe.ok && path.startsWith("/api/auth/candidate/")) return probe;
      await probe.body?.cancel();
    } catch (error) {
      if (path.startsWith("/api/auth/candidate/")) throw error;
      // An unavailable native provision must not disable unenrolled legacy
      // accounts. Their backend branch still enforces its own access policy.
    }
  }
  return send(path, init, estimatedTime(scope));
}
