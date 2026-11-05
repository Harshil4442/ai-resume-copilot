import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { isTrustedRequestOrigin } from "../../../../lib/requestOrigin";
import { handleAccount, SAFE_ACCOUNT_ALIASES } from "../../../../lib/accountTransportServer";
import { candidateAuthSecret } from "../../../../lib/candidateAuthServer";
import { validCanaryChoice, validCanarySelectors } from "../../../../lib/canaryParticipation";
import { catalogTimingHeaders } from "../../../../lib/catalogTiming";


const FORWARDED_HEADERS = ["accept", "content-type", "idempotency-key", "x-correlation-id"];
const MUTATION_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const NOTIFICATION_BODY_LIMIT = 8192;
const NOTIFICATION_READ_MS = 10_000;

function notificationRoute(path: string[]) {
  const exact = path.join("/");
  return exact === "v1/notifications/preferences" ? "preferences"
    : exact === "public/notifications/unsubscribe" ? "unsubscribe" : null;
}

class NotificationBodyError extends Error {
  constructor(readonly status: number) { super("Invalid notification request"); }
}

async function notificationBody(request: NextRequest): Promise<ArrayBuffer> {
  const length = request.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)))) throw new NotificationBodyError(422);
  if (length !== null && Number(length) > NOTIFICATION_BODY_LIMIT) throw new NotificationBodyError(413);
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json"
    || request.headers.has("content-encoding") || !request.body || request.signal.aborted) throw new NotificationBodyError(422);
  const reader = request.body.getReader();
  const expires = performance.now() + NOTIFICATION_READ_MS;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort = () => {};
  let finished = false;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new NotificationBodyError(408)), NOTIFICATION_READ_MS);
    abort = () => reject(new NotificationBodyError(408));
    request.signal.addEventListener("abort", abort, { once: true });
  });
  try {
    const chunks: Uint8Array[] = [];
    let size = 0;
    let reads = 0;
    while (true) {
      if (request.signal.aborted || performance.now() >= expires) throw new NotificationBodyError(408);
      if (++reads > NOTIFICATION_BODY_LIMIT + 1) throw new NotificationBodyError(422);
      const part = await Promise.race([reader.read(), deadline]);
      if (request.signal.aborted || performance.now() >= expires) throw new NotificationBodyError(408);
      if (part.done) { finished = true; break; }
      size += part.value.byteLength;
      if (size > NOTIFICATION_BODY_LIMIT) throw new NotificationBodyError(413);
      if (part.value.byteLength) chunks.push(part.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new NotificationBodyError(422);
    return bytes.buffer;
  } catch (error) {
    throw error instanceof NotificationBodyError ? error : new NotificationBodyError(422);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    request.signal.removeEventListener("abort", abort);
    // An untrusted stream's cancellation promise must never extend the read deadline.
    if (!finished) { try { void reader.cancel().catch(() => {}); } catch { /* No input reflection. */ } }
    try { reader.releaseLock(); } catch { /* A cancelled pending read owns no authority. */ }
  }
}

function backendOrigin() {
  const configured = process.env.BACKEND_URL?.trim().replace(/\/+$/, "");
  if (configured) return configured.endsWith("/api") ? configured.slice(0, -4) : configured;
  if (process.env.NODE_ENV === "production") {
    throw new Error("BACKEND_URL is required in production");
  }
  return "http://127.0.0.1:8000";
}

function isPublicPath(path: string[]) {
  return path[0] === "public";
}

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  if (MUTATION_METHODS.has(request.method) && !isTrustedRequestOrigin(request)) {
    return NextResponse.json({ detail: "Cross-site mutation requests are not allowed" }, { status: 403 });
  }
  const { path } = await context.params;
  // Authentication is never a generic proxy operation. In particular a
  // caller cannot obtain an issuer bearer or pick a private logout request.
  if (path[0] === "auth") {
    // These exact compatibility aliases terminate in the dedicated handler;
    // they cannot select an issuer/native lifecycle path or stream its reply.
    if (path.length === 2 && Object.hasOwn(SAFE_ACCOUNT_ALIASES, path[1])) {
      return handleAccount(request, SAFE_ACCOUNT_ALIASES[path[1]], true);
    }
    return NextResponse.json({ detail: "Use the dedicated account transport" }, { status: 403,
      headers: { "Cache-Control": "private, no-store" } });
  }
  if (path[0] === "v1" && path[1] === "browser-pairing") {
    return NextResponse.json({ detail: "Use the dedicated browser pairing transport" }, { status: 403 });
  }
  if (path[0] === "v1" && path[1] === "browser-assistance" && path[2] !== "availability") {
    return NextResponse.json({ detail: "Use the dedicated reviewed browser assistance transport" }, { status: 403 });
  }
  if (path[0] === "v1" && path[1] === "employer-jobs" && path[2] === "applications" && path[4] === "report-submitted") {
    return NextResponse.json({ detail: "Use the dedicated candidate report transport" }, { status: 403 });
  }
  if (!path.length || path.length > 20 || path.some((segment) => segment === "." || segment === ".."
      || segment.length > 256 || !/^[A-Za-z0-9_.-]+$/.test(segment))) {
    return NextResponse.json({ detail: "Invalid backend path" }, { status: 400 });
  }

  const notification = notificationRoute(path);
  if (notification) {
    const allowed = notification === "preferences" ? ["GET", "PUT"] : ["POST"];
    if (!allowed.includes(request.method)) return NextResponse.json({ detail: "Unsupported notification request" }, {
      status: 405, headers: { Allow: allowed.join(", "), "Cache-Control": "private, no-store" },
    });
    if (request.nextUrl.search) return NextResponse.json({ detail: "Invalid notification selector" }, {
      status: 422, headers: { "Cache-Control": "private, no-store" },
    });
  }

  const canary = path[0] === "v1" && path[1] === "credits" && path[2] === "canary-enrollment";
  if (canary && (path.length !== 3 || !["GET", "POST"].includes(request.method))) {
    return NextResponse.json({ detail: "Unsupported participation request" }, { status: 405, headers: { Allow: "GET, POST", "Cache-Control": "private, no-store" } });
  }
  if (canary && (request.method === "POST" ? [...request.nextUrl.searchParams].length !== 0 : !validCanarySelectors(request.nextUrl.searchParams))) {
    return NextResponse.json({ detail: "Invalid participation selector" }, { status: 422 });
  }
  const isCatalog = request.method === "GET" && path.join("/") === "v1/employer-jobs/catalog";
  const sessionStarted = isCatalog ? performance.now() : 0;
  // A purpose-isolated email proof never depends on a login cookie or its parser.
  const token = notification === "unsubscribe" ? null : await getToken({ req: request, secret: candidateAuthSecret() });
  const sessionMs = isCatalog ? performance.now() - sessionStarted : 0;
  const accessToken = typeof token?.accessToken === "string" ? token.accessToken : null;
  if (!isPublicPath(path) && !accessToken) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const target = new URL(`/api/${path.map(encodeURIComponent).join("/")}`, backendOrigin());
  request.nextUrl.searchParams.forEach((value, key) => target.searchParams.append(key, value));

  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (isCatalog) headers.delete("x-correlation-id");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  let body: ArrayBuffer | undefined;
  if (notification && hasBody) {
    try { body = await notificationBody(request); }
    catch (error) {
      return NextResponse.json({ detail: "Invalid notification request" }, {
        status: error instanceof NotificationBodyError ? error.status : 422,
        headers: { "Cache-Control": "private, no-store" },
      });
    }
  } else if (canary && request.method === "POST") {
    const reader = request.body?.getReader();
    try {
      if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json" || !reader) throw new Error("Invalid choice");
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.byteLength;
        if (length > 2048) { await reader.cancel(); throw new Error("Invalid choice"); }
        chunks.push(part.value);
      }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      if (!validCanaryChoice(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)))) throw new Error("Invalid choice");
      body = bytes.buffer;
    } catch {
      try { await reader?.cancel(); } catch { /* Release the invalid input without reflecting it. */ }
      return NextResponse.json({ detail: "Invalid participation request" }, { status: 422, headers: { "Cache-Control": "private, no-store" } });
    } finally { reader?.releaseLock(); }
  } else if (hasBody) body = await request.arrayBuffer();
  const backendStarted = isCatalog ? performance.now() : 0;
  // Private document inspection has a 75-second HTTP budget before the
  // parse response is saved. Leave headroom only for this exact upload route.
  const upstreamTimeoutMs = request.method === "POST" && path.length === 2
    && path[0] === "resume" && path[1] === "parse" ? 120_000 : 65_000;
  try {
    const response = await fetch(target, {
      method: request.method,
      redirect: "error",
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(upstreamTimeoutMs),
    });
    const responseHeaders = new Headers({
      "Cache-Control": "private, no-store, max-age=0",
      Pragma: "no-cache",
    });
    for (const name of ["content-type", "content-disposition", "x-correlation-id", "retry-after"]) {
      if (isCatalog && name === "x-correlation-id") continue;
      const value = response.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    if (isCatalog) {
      catalogTimingHeaders(response.headers, sessionMs, performance.now() - backendStarted)
        .forEach((value, name) => responseHeaders.set(name, value));
    }
    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    const response = NextResponse.json(
      { detail: timedOut ? "The backend request timed out" : "The backend is unavailable" },
      { status: timedOut ? 504 : 502 },
    );
    if (isCatalog) {
      catalogTimingHeaders(null, sessionMs, null)
        .forEach((value, name) => response.headers.set(name, value));
    }
    return response;
  }
}

export const dynamic = "force-dynamic";
export const maxDuration = 150;

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
// Preserve Next's prior automatic HEAD behavior on every other generic path.
export const HEAD = forward;

export async function OPTIONS(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (notificationRoute(path)) return forward(request, { params: Promise.resolve({ path }) });
  // Match the pinned Next default for this route's pre-existing method exports.
  return new Response(null, { status: 204, headers: { Allow: "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT" } });
}
