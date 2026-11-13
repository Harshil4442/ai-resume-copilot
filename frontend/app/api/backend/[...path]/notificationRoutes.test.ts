// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ports = vi.hoisted(() => ({ getToken: vi.fn(), authSecret: vi.fn(), account: vi.fn() }));
vi.mock("next-auth/jwt", () => ({ getToken: ports.getToken }));
vi.mock("../../../../lib/candidateAuthServer", () => ({ candidateAuthSecret: ports.authSecret }));
vi.mock("../../../../lib/accountTransportServer", () => ({ handleAccount: ports.account, SAFE_ACCOUNT_ALIASES: {} }));

import * as route from "./route";

const SITE = "https://www.hirewizhq.com";
const PRIVATE = ["v1", "notifications", "preferences"];
const PUBLIC = ["public", "notifications", "unsubscribe"];
const backend = vi.fn();
const encode = (value: string) => new TextEncoder().encode(value);
type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS";

function incoming(method: Method, path = PUBLIC, body: string | Uint8Array | ReadableStream<Uint8Array> = '{"token":"PRIVATE_PROOF"}',
  headers: Record<string, string> = {}, query = "") {
  return new NextRequest(`${SITE}/api/backend/${path.join("/")}${query}`, {
    method, headers: { origin: SITE, "content-type": "application/json", ...headers },
    ...(["GET", "HEAD"].includes(method) ? {} : { body: body as BodyInit, duplex: "half" }),
  } as NonNullable<ConstructorParameters<typeof NextRequest>[1]> & { duplex?: "half" });
}

function call(method: Method, request: NextRequest, path = PUBLIC) {
  return route[method](request, { params: Promise.resolve({ path }) });
}

function unread(request: NextRequest) {
  const access = vi.fn(() => { throw new Error("PRIVATE_BODY_READ"); });
  Object.defineProperty(request, "body", { get: access });
  vi.spyOn(request, "arrayBuffer").mockImplementation(access);
  return access;
}

function streamed(chunks: Uint8Array[], cancel = vi.fn()) {
  return { cancel, body: new ReadableStream<Uint8Array>({
    start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close(); }, cancel,
  }) };
}

async function fixedError(response: Response, status: number) {
  expect(response.status).toBe(status);
  expect(await response.json()).toEqual({ detail: "Invalid notification request" });
  expect(response.headers.get("cache-control")).toContain("no-store");
  expect(backend).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.stubEnv("BACKEND_URL", "https://backend.example.test");
  ports.getToken.mockReset().mockResolvedValue({ accessToken: "synthetic-session-bearer" });
  ports.authSecret.mockReset().mockReturnValue("synthetic-cookie-key");
  ports.account.mockReset();
  backend.mockReset().mockImplementation(async () => new Response('{"weekly_digest":false}',
    { headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", backend);
});
afterEach(() => {
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks();
  vi.unstubAllGlobals(); vi.unstubAllEnvs();
});

describe("exact notification route guard", () => {
  it.each(["GET", "PUT"] as const)("preserves authenticated preferences %s with the session bearer", async (method) => {
    const request = incoming(method, PRIVATE, '{"expected_generation":1,"weekly_digest":false,"explicit_confirmation":true}',
      { authorization: "Bearer client-controlled" });
    expect((await call(method, request, PRIVATE)).status).toBe(200);
    expect(ports.getToken).toHaveBeenCalledOnce(); expect(ports.authSecret).toHaveBeenCalledOnce();
    expect(backend).toHaveBeenCalledOnce();
    const [target, options] = backend.mock.calls[0];
    expect(String(target)).toBe("https://backend.example.test/api/v1/notifications/preferences");
    expect(options.headers.get("authorization")).toBe("Bearer synthetic-session-bearer");
    expect(options.cache).toBe("no-store"); expect(options.redirect).toBe("error");
  });

  it("public unsubscribe never parses even malformed cookies or asks for the auth secret", async () => {
    ports.getToken.mockImplementation(() => { throw new Error("PRIVATE_COOKIE_FAILURE"); });
    ports.authSecret.mockImplementation(() => { throw new Error("PRIVATE_SECRET_FAILURE"); });
    const request = incoming("POST", PUBLIC, '{"token":"PRIVATE_PROOF"}', {
      cookie: "next-auth.session-token=%broken; __Secure-next-auth.session-token=bad",
      authorization: "Bearer attacker-controlled",
    });
    expect((await call("POST", request)).status).toBe(200);
    expect(ports.getToken).not.toHaveBeenCalled(); expect(ports.authSecret).not.toHaveBeenCalled();
    expect(backend).toHaveBeenCalledOnce();
    const [target, options] = backend.mock.calls[0];
    expect(String(target)).toBe("https://backend.example.test/api/public/notifications/unsubscribe");
    expect(options.headers.has("authorization")).toBe(false);
    expect(options.headers.has("cookie")).toBe(false);
    expect(new TextDecoder().decode(options.body)).toBe('{"token":"PRIVATE_PROOF"}');
  });

  it.each(["POST", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const)("rejects preferences %s before auth or body", async (method) => {
    const request = incoming(method, PRIVATE), access = unread(request);
    const response = await call(method, request, PRIVATE);
    expect(response.status).toBe(405); expect(response.headers.get("allow")).toBe("GET, PUT");
    expect(access).not.toHaveBeenCalled(); expect(ports.getToken).not.toHaveBeenCalled();
    expect(ports.authSecret).not.toHaveBeenCalled(); expect(backend).not.toHaveBeenCalled();
  });

  it.each(["GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const)("rejects public unsubscribe %s before auth or body", async (method) => {
    const request = incoming(method), access = unread(request);
    const response = await call(method, request);
    expect(response.status).toBe(405); expect(response.headers.get("allow")).toBe("POST");
    expect(access).not.toHaveBeenCalled(); expect(ports.getToken).not.toHaveBeenCalled();
    expect(ports.authSecret).not.toHaveBeenCalled(); expect(backend).not.toHaveBeenCalled();
  });

  it.each([["GET", PRIVATE], ["PUT", PRIVATE], ["POST", PUBLIC]] as const)("rejects query selectors on %s before auth/body", async (method, path) => {
    const request = incoming(method, [...path], undefined, {}, "?token=PRIVATE_QUERY&owner_id=2");
    const access = unread(request);
    const response = await call(method, request, [...path]);
    expect(response.status).toBe(422); expect(await response.text()).not.toContain("PRIVATE_QUERY");
    expect(access).not.toHaveBeenCalled(); expect(ports.getToken).not.toHaveBeenCalled();
    expect(ports.authSecret).not.toHaveBeenCalled(); expect(backend).not.toHaveBeenCalled();
  });

  it.each([["PUT", PRIVATE], ["POST", PUBLIC]] as const)("retains the mutation origin gate for %s", async (method, path) => {
    const request = incoming(method, [...path], undefined, { origin: "https://foreign.example", "sec-fetch-site": "cross-site" });
    expect((await call(method, request, [...path])).status).toBe(403);
    expect(ports.getToken).not.toHaveBeenCalled(); expect(ports.authSecret).not.toHaveBeenCalled();
    expect(backend).not.toHaveBeenCalled();
  });

  it("requires a private bearer before reading preference mutation bytes", async () => {
    ports.getToken.mockResolvedValue(null);
    const request = incoming("PUT", PRIVATE), access = unread(request);
    expect((await call("PUT", request, PRIVATE)).status).toBe(401);
    expect(access).not.toHaveBeenCalled(); expect(backend).not.toHaveBeenCalled();
  });
});

describe("bounded notification body", () => {
  it.each([false, true])("forwards exact8192 original bytes with optional Content-Length %s", async (declared) => {
    const raw = '{"token":"PRIVATE_PROOF"}' + " ".repeat(8192-'{"token":"PRIVATE_PROOF"}'.length);
    const input = streamed([encode(raw.slice(0, 4096)), encode(raw.slice(4096))]);
    expect((await call("POST", incoming("POST", PUBLIC, input.body, declared ? { "content-length": "8192" } : {}))).status).toBe(200);
    expect(backend).toHaveBeenCalledOnce();
    expect(new TextDecoder().decode(backend.mock.calls[0][1].body)).toBe(raw);
  });

  it.each([undefined, "1"])("counts overflow from actual streamed bytes instead of trusting length %s", async (length) => {
    const input = streamed([new Uint8Array(4096), new Uint8Array(4097)]);
    await fixedError(await call("POST", incoming("POST", PUBLIC, input.body,
      length === undefined ? {} : { "content-length": length })), 413);
  });

  it("counts UTF-8 bytes rather than JavaScript characters", async () => {
    const raw = JSON.stringify({ token: "é".repeat(4100) });
    expect(raw.length).toBeLessThan(8192); expect(encode(raw).byteLength).toBeGreaterThan(8192);
    await fixedError(await call("POST", incoming("POST", PUBLIC, raw)), 413);
  });

  it("rejects declared oversized bodies without reading their stream", async () => {
    const request = incoming("POST", PUBLIC, "{}", { "content-length": "8193" }), access = unread(request);
    await fixedError(await call("POST", request), 413); expect(access).not.toHaveBeenCalled();
  });

  it.each(["-1", "one", "1.5", "9007199254740992"])("rejects invalid length metadata %s", async (length) => {
    await fixedError(await call("POST", incoming("POST", PUBLIC, "{}", { "content-length": length })), 422);
  });

  it.each(["", "{", '{"token":"PRIVATE_PROOF"}trailing', "null", "[]", '"PRIVATE_PROOF"',
    '{"token":NaN}'])("rejects invalid/nonobject JSON case %# without reflecting it", async (raw) => {
    await fixedError(await call("POST", incoming("POST", PUBLIC, raw)), 422);
  });

  it("rejects malformed UTF-8 even when byte replacement could form valid JSON", async () => {
    const raw = new Uint8Array([...encode('{"token":"'), 0xff, ...encode('"}')]);
    await fixedError(await call("POST", incoming("POST", PUBLIC, raw)), 422);
  });

  it.each<Record<string, string>>([{ "content-type": "text/plain" }, { "content-type": "" },
    { "content-encoding": "gzip" }, { "content-encoding": "br" }])("rejects unaccepted media/encoding %#", async (headers) => {
    await fixedError(await call("POST", incoming("POST", PUBLIC, "{}", headers)), 422);
  });

  it("does not wait for a hostile cancellation promise on an oversized request", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn(() => new Promise<void>(() => {}));
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new Uint8Array(8193)); }, cancel });
    const operation = call("POST", incoming("POST", PUBLIC, body));
    const raced = Promise.race([operation, new Promise<null>((resolve) => setTimeout(() => resolve(null), 1))]);
    await vi.advanceTimersByTimeAsync(1);
    const response = await raced;
    expect(response).not.toBeNull(); await fixedError(response!, 413); expect(cancel).toHaveBeenCalledOnce();
  });

  it("uses one absolute10s deadline across trickling chunks and does not await cancel", async () => {
    vi.useFakeTimers();
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const cancel = vi.fn(() => new Promise<void>(() => {}));
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; value.enqueue(encode('{"token":"')); }, cancel });
    for (const milliseconds of [3000, 6000, 9000]) setTimeout(() => controller.enqueue(encode("x")), milliseconds);
    let observed: Response | undefined;
    const operation = call("POST", incoming("POST", PUBLIC, body)).then((response) => { observed = response; return response; });
    await vi.advanceTimersByTimeAsync(9999);
    expect(observed).toBeUndefined(); expect(backend).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(observed?.status).toBe(408);
    await fixedError(await operation, 408); expect(cancel).toHaveBeenCalledOnce();
  });

  it("rejects late EOF even if the deadline timer has not had an event-loop turn", async () => {
    vi.useFakeTimers();
    let now = 500;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; value.enqueue(encode("{}")); } });
    const operation = call("POST", incoming("POST", PUBLIC, body));
    await vi.advanceTimersByTimeAsync(0);
    expect(backend).not.toHaveBeenCalled();
    now = 10_501;
    controller.close();
    // Fake timer time remains zero: only the post-read monotonic check can
    // reject this completed body before forwarding it to the backend.
    await fixedError(await operation, 408);
  });

  it("bounds zero-byte stream floods instead of accumulating empty chunks forever", async () => {
    const cancel = vi.fn();
    let pulls = 0;
    const body = new ReadableStream<Uint8Array>({ pull(controller) { pulls++; controller.enqueue(new Uint8Array()); }, cancel });
    await fixedError(await call("POST", incoming("POST", PUBLIC, body)), 422);
    expect(pulls).toBeLessThanOrEqual(8195); expect(cancel).toHaveBeenCalledOnce();
  });
});

describe("unchanged unrelated public route", () => {
  it("preserves ordinary public GET forwarding and automatic-style OPTIONS", async () => {
    ports.getToken.mockResolvedValue(null);
    const path = ["public", "billing", "catalog"];
    expect((await call("GET", incoming("GET", path), path)).status).toBe(200);
    expect(backend).toHaveBeenCalledOnce();
    expect(backend.mock.calls[0][1].headers.has("authorization")).toBe(false);
    backend.mockClear(); ports.getToken.mockClear(); ports.authSecret.mockClear();
    const response = await call("OPTIONS", incoming("OPTIONS", path), path);
    expect(response.status).toBe(204); expect(response.headers.get("allow")).toBe("DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT");
    expect(backend).not.toHaveBeenCalled(); expect(ports.getToken).not.toHaveBeenCalled();
    expect(ports.authSecret).not.toHaveBeenCalled();
  });
});
