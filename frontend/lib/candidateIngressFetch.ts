/** Server-only clock estimates; every request still needs fresh backend admission. */
import { candidateIngressHeaders } from "./candidateIngressServer";

const AVAILABILITY = "/api/auth/candidate/v1/availability";
const CLOCK_ESTIMATE_AGE_MS = 30_000;
type ClockEstimate = { scope: string; issued: number; started: number };
let clock: ClockEstimate | undefined;

function estimatedTime(scope: string) {
  if (!clock || clock.scope !== scope) return undefined;
  const elapsed = performance.now() - clock.started;
  if (elapsed < 0 || elapsed > CLOCK_ESTIMATE_AGE_MS) return undefined;
  return clock.issued + Math.floor(elapsed);
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
