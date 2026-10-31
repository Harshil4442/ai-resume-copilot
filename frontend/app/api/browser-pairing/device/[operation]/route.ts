import { NextRequest, NextResponse } from "next/server";
import { BROWSER_BACKEND_TIMEOUT_MS } from "../../../../../lib/browserTransportTimeouts";
import { boundedPairingBytes, DEVICE_OPERATIONS, deviceReply, gatewayConfiguration, MAX_PAIRING_BODY, MAX_PAIRING_RESPONSE } from "../../../../../lib/browserPairingGateway";

const headers = { "Cache-Control": "private, no-store, max-age=0", Pragma: "no-cache", "X-Content-Type-Options": "nosniff" };
const readinessOperations = new Set(["prepare", "inspect-key", "status", "challenge", "refresh-challenge"]);
const readinessBackends = new Set(["https://ai-resume-parser-mqff62d75a-uc.a.run.app", "https://ai-resume-parser-157225724590.us-central1.run.app"]);
const extensionOrigin = "chrome-extension://pnjmegppacleipajnifjmbcopjjffklb";
const readinessTimeoutMs = 15000;

// Public availability only: no clock, device identity, admission or claim is issued.
// Privileged extension GETs may omit Origin; a supplied Origin is still filtered.
export async function GET(request: NextRequest, context: { params: Promise<{ operation: string }> }) {
  try {
    const { operation } = await context.params;
    const origin = request.headers.get("origin");
    if (request.method !== "GET" || !readinessOperations.has(operation) || request.nextUrl.search || request.body
        || request.headers.has("cookie") || request.headers.has("authorization") || request.headers.has("content-type")
        || request.headers.has("content-length") || request.headers.has("content-encoding") || request.headers.has("transfer-encoding")
        || origin !== null && origin !== extensionOrigin) return new NextResponse(null, { status: 403, headers });
    const configuration = gatewayConfiguration();
    if (configuration.origin !== "https://www.hirewizhq.com" || request.nextUrl.origin !== configuration.origin
        || !readinessBackends.has(configuration.backend)) return new NextResponse(null, { status: 503, headers });
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(readinessTimeoutMs)]);
    signal.throwIfAborted();
    const response = await fetch(`${configuration.backend}/api/health`, {
      method: "GET", credentials: "omit", redirect: "error", cache: "no-store", signal });
    // The public health body and cancellation acknowledgement are never consumed.
    try { void response.body?.cancel().catch(() => undefined); } catch { /* best effort */ }
    signal.throwIfAborted();
    return new NextResponse(null, { status: response.status === 200 ? 204 : 503, headers });
  } catch {
    return new NextResponse(null, { status: 503, headers });
  }
}

export async function POST(request: NextRequest, context: { params: Promise<{ operation: string }> }) {
  try {
    const { operation } = await context.params;
    if (!DEVICE_OPERATIONS.has(operation) || request.nextUrl.search || request.headers.get("cookie")
        || request.headers.get("authorization") || request.headers.get("content-type") !== "application/json"
        || request.headers.get("content-encoding")
        || !/^chrome-extension:\/\/[a-p]{32}$/.test(request.headers.get("origin") ?? "")) {
      return NextResponse.json({ detail: "Browser pairing request was rejected" }, { status: 403, headers });
    }
    const configuration = gatewayConfiguration();
    const raw = await boundedPairingBytes(request.body, MAX_PAIRING_BODY);
    const response = await fetch(`${configuration.backend}/api/v1/browser-pairing/device/${operation}`, {
      method: "POST", credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(BROWSER_BACKEND_TIMEOUT_MS),
      headers: { "Content-Type": "application/json", Origin: request.headers.get("origin")! }, body: new Uint8Array(raw) });
    if (!response.ok) {
      // Arbitrary cancellation acknowledgements cannot delay or remap this error.
      try { void response.body?.cancel().catch(() => undefined); } catch { /* best effort */ }
      return NextResponse.json({ detail: "Current browser pairing is unavailable" }, { status: response.status === 403 ? 403 : 503, headers });
    }
    const body = await boundedPairingBytes(response.body, MAX_PAIRING_RESPONSE);
    return NextResponse.json(deviceReply(operation, JSON.parse(body.toString("utf8"))), { headers });
  } catch {
    return NextResponse.json({ detail: "Browser pairing is currently unavailable" }, { status: 503, headers });
  }
}
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 45;
