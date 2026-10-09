import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";
import { isTrustedRequestOrigin } from "../../../../lib/requestOrigin";
import { catalogTimingHeaders } from "../../../../lib/catalogTiming";

const PUBLIC_PATHS = new Set(["auth/register"]);
const FORWARDED_HEADERS = ["accept", "content-type", "idempotency-key", "x-correlation-id"];
const MUTATION_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function backendOrigin() {
  const configured = process.env.BACKEND_URL?.trim().replace(/\/+$/, "");
  if (configured) return configured.endsWith("/api") ? configured.slice(0, -4) : configured;
  if (process.env.NODE_ENV === "production") {
    throw new Error("BACKEND_URL is required in production");
  }
  return "http://127.0.0.1:8000";
}

function isPublicPath(path: string[]) {
  const joined = path.join("/");
  return PUBLIC_PATHS.has(joined) || path[0] === "public";
}

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  if (MUTATION_METHODS.has(request.method) && !isTrustedRequestOrigin(request)) {
    return NextResponse.json({ detail: "Cross-site mutation requests are not allowed" }, { status: 403 });
  }
  const { path } = await context.params;
  if (path[0] === "v1" && path[1] === "browser-pairing") {
    return NextResponse.json({ detail: "Use the dedicated browser pairing transport" }, { status: 403 });
  }
  if (!path.length || path.some((segment) => segment === ".." || segment.includes("/"))) {
    return NextResponse.json({ detail: "Invalid backend path" }, { status: 400 });
  }

  const isCatalog = request.method === "GET" && path.join("/") === "v1/employer-jobs/catalog";
  const sessionStarted = isCatalog ? performance.now() : 0;
  const token = await getToken({ req: request, secret: process.env.NEXTAUTH_SECRET });
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
  const body = hasBody ? await request.arrayBuffer() : undefined;
  const backendStarted = isCatalog ? performance.now() : 0;
  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(65_000),
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

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
