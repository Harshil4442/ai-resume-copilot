import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = new Set(["auth/register"]);
const FORWARDED_HEADERS = ["accept", "content-type", "idempotency-key", "x-correlation-id"];

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
  const { path } = await context.params;
  if (!path.length || path.some((segment) => segment === ".." || segment.includes("/"))) {
    return NextResponse.json({ detail: "Invalid backend path" }, { status: 400 });
  }

  const token = await getToken({ req: request, secret: process.env.NEXTAUTH_SECRET });
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

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const body = hasBody ? await request.arrayBuffer() : undefined;
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
      const value = response.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return NextResponse.json(
      { detail: timedOut ? "The backend request timed out" : "The backend is unavailable" },
      { status: timedOut ? 504 : 502 },
    );
  }
}

export const dynamic = "force-dynamic";

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
