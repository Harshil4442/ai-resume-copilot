import { NextRequest, NextResponse } from "next/server";
import { boundedPairingBytes, DEVICE_OPERATIONS, deviceReply, gatewayConfiguration, MAX_PAIRING_BODY, MAX_PAIRING_RESPONSE } from "../../../../../lib/browserPairingGateway";

const headers = { "Cache-Control": "private, no-store, max-age=0", Pragma: "no-cache", "X-Content-Type-Options": "nosniff" };
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
      method: "POST", credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(12000),
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
