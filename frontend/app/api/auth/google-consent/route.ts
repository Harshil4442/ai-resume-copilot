import { NextRequest, NextResponse } from "next/server";

const POLICY_VERSION = "2026-07-11";
const COOKIE_NAME = "hirewiz_google_registration_consent";

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== request.nextUrl.origin) {
    return NextResponse.json({ detail: "Invalid request origin." }, { status: 403 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request." }, { status: 400 });
  }
  const consent = body as Record<string, unknown>;
  if (
    consent.accepted_terms !== true ||
    consent.confirmed_age_18 !== true ||
    consent.policy_version !== POLICY_VERSION
  ) {
    return NextResponse.json({ detail: "Registration consent is required." }, { status: 400 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, POLICY_VERSION, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 10 * 60,
  });
  response.headers.set("Cache-Control", "no-store, private");
  return response;
}
