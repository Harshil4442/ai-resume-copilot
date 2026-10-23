import { NextRequest, NextResponse } from "next/server";
import { AUTH_BODY_LIMIT, backendAuth, csrfMatches, websiteOrigin } from "../../../../lib/candidateAuthServer";
import { boundedPairingBytes } from "../../../../lib/browserPairingGateway";

export const runtime = "nodejs";
const HEADERS = { "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff" };
const TOKEN = /^[A-Za-z0-9_-]{43}$/;
type Context = { params: Promise<{ operation: string }> };

function reply(status: number, code: string) {
  return NextResponse.json({ status: code }, { status, headers: HEADERS });
}

function inputObject(text: string): Record<string, string> {
  // This operation accepts only a flat string object. Parse each field once so
  // duplicate keys and unexpected values cannot disappear in JSON.parse.
  let offset = 0;
  const fields: Record<string, string> = Object.create(null);
  const whitespace = () => { while (/[\t\r\n ]/.test(text[offset] || "x")) offset++; };
  const consume = (value: string) => { whitespace(); if (text[offset++] !== value) throw new Error(); };
  const string = () => {
    whitespace();
    const match = /^"(?:[^"\\\u0000-\u001f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/.exec(text.slice(offset));
    if (!match) throw new Error();
    offset += match[0].length;
    return JSON.parse(match[0]) as string;
  };
  consume("{"); whitespace();
  if (text[offset] !== "}") {
    for (;;) {
      const key = string(); consume(":");
      if (Object.hasOwn(fields, key)) throw new Error();
      fields[key] = string(); whitespace();
      if (text[offset] !== ",") break;
      offset++;
    }
  }
  consume("}"); whitespace();
  if (offset !== text.length) throw new Error();
  return fields;
}

export async function POST(request: NextRequest, context: Context) {
  const operation = (await context.params).operation;
  if (!["request", "confirm"].includes(operation)) return reply(404, "RESET_REQUEST_REJECTED");
  try {
    if (request.headers.get("origin") !== websiteOrigin(request) || request.nextUrl.search
        || request.headers.get("content-type") !== "application/json" || request.headers.has("content-encoding")
        || !csrfMatches(request, request.headers.get("x-hirewiz-account-csrf") || "")) return reply(403, "RESET_REQUEST_REJECTED");
    const raw = await boundedPairingBytes(request.body, AUTH_BODY_LIMIT);
    let input: Record<string, string>;
    try {
      input = inputObject(new TextDecoder("utf8", { fatal: true }).decode(raw));
      const keys = Object.keys(input).sort().join(",");
      if (operation === "request") {
        if (keys !== "email" || input.email.length > 320 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.email)) throw new Error();
      } else if (keys !== "new_password,token" || !TOKEN.test(input.token)
          || Array.from(input.new_password).length < 10 || Array.from(input.new_password).length > 128) throw new Error();
    } catch { return reply(422, "RESET_REQUEST_REJECTED"); }
    const result = await backendAuth(`password-reset/${operation}`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
    const exact = Object.keys(result.data).join(",") === "status";
    if (operation === "request" && result.status === 202 && exact && result.data.status === "RESET_LINK_REQUESTED") {
      return reply(202, "RESET_LINK_REQUESTED");
    }
    if (operation === "confirm" && result.status === 200 && exact && result.data.status === "PASSWORD_RESET") {
      return reply(200, "PASSWORD_RESET");
    }
    if (operation === "confirm" && result.status === 400 && exact && result.data.status === "RESET_LINK_INVALID") {
      return reply(400, "RESET_LINK_INVALID");
    }
    if (result.status === 429) return reply(429, "RATE_LIMITED");
    if (result.status === 422) return reply(422, "RESET_REQUEST_REJECTED");
    return reply(503, "RESET_UNAVAILABLE");
  } catch { return reply(503, "RESET_UNAVAILABLE"); }
}
