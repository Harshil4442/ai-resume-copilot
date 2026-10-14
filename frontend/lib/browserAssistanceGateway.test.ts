// @vitest-environment node
import { createHash, generateKeyPairSync } from "node:crypto";
import { NextRequest } from "next/server";
import { getToken } from "next-auth/jwt";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { browserAssistanceGateway } from "./browserAssistanceGateway";

vi.mock("next-auth/jwt", () => ({ getToken: vi.fn() }));
const origin = "https://hirewiz.example";
const secret = "synthetic-nextauth-secret-for-boundary-controls";
const csrf = "a".repeat(64);
const deviceOrigin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop";
const identity = "00000000-0000-4000-8000-000000000001";
const backendBearer = "synthetic-private-hirewiz-bearer";
const upstream = vi.fn();
const { publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
const publicDevice = { id: identity, state: "pending", key_sha256: "b".repeat(64), extension_id: "abcdefghijklmnopabcdefghijklmnop",
  expires_at_ms: Date.now() + 60000, authority_key: publicKey.export({ format: "jwk" }) };
type Options = { device?: boolean; anonymous?: boolean; origin?: string; cookie?: string | null; csrf?: string | null; query?: string; headers?: Record<string, string>; body?: string | Uint8Array<ArrayBuffer> };
function request(operation: string, options: Options = {}): NextRequest {
  const headers = new Headers({ "Content-Type": "application/json", Origin: options.origin ?? (options.device ? deviceOrigin : origin), ...options.headers });
  if (!options.device) {
    headers.set("Cookie", options.cookie ?? `${options.anonymous ? "" : "__Secure-next-auth.session-token=synthetic-google-account-cookie; "}__Host-next-auth.csrf-token=${encodeURIComponent(`${csrf}|${createHash("sha256").update(csrf + secret).digest("hex")}`)}`);
    if (options.csrf !== null) headers.set("X-Hirewiz-Account-CSRF", options.csrf ?? csrf);
    headers.set("Sec-Fetch-Site", options.headers?.["Sec-Fetch-Site"] ?? "same-origin");
  } else if (options.cookie !== undefined && options.cookie !== null) headers.set("Cookie", options.cookie);
  return new NextRequest(`${origin}/api/browser-assistance/${options.device ? "device" : "candidate"}/${operation}${options.query ?? ""}`,
    { method: "POST", headers, body: options.body ?? JSON.stringify({ identity }) });
}
async function candidate(operation = "device", options: Options = {}) { return browserAssistanceGateway(request(operation, options), operation, false); }
async function device(operation = "status", options: Options = {}) { return browserAssistanceGateway(request(operation, { ...options, device: true }), operation, true); }
function privateResponse(response: Response) {
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  expect(response.headers.get("Pragma")).toBe("no-cache");
  expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
}
beforeEach(() => {
  vi.resetAllMocks(); vi.stubEnv("NEXTAUTH_URL", origin); vi.stubEnv("NEXTAUTH_SECRET", secret); vi.stubEnv("BACKEND_URL", "https://api.example/api/");
  vi.mocked(getToken).mockResolvedValue({ accessToken: backendBearer, hirewizUserId: 12, googleAccessToken: "synthetic-google-provider-token", refreshToken: "synthetic-provider-refresh" });
  upstream.mockResolvedValue(Response.json(publicDevice)); vi.stubGlobal("fetch", upstream);
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("browser assistance NextAuth and device transport boundaries", () => {
  it("forwards the current Google-linked HireWiz bearer only on the candidate route, with no cookies/provider tokens in its reply", async () => {
    const response = await candidate(); const clientReply = await response.json(); expect(response.status).toBe(200); expect(clientReply).toEqual(publicDevice); privateResponse(response);
    expect(getToken).toHaveBeenCalledTimes(1); expect(upstream).toHaveBeenCalledTimes(1);
    const [url, init] = upstream.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example/api/v1/browser-assistance/candidate/device");
    const headers = new Headers(init.headers); expect(headers.get("Authorization")).toBe(`Bearer ${backendBearer}`);
    expect(headers.has("Cookie")).toBe(false); expect(headers.has("X-Hirewiz-Account-CSRF")).toBe(false); expect(headers.has("Origin")).toBe(false);
    expect(init.credentials).toBe("omit"); expect(init.redirect).toBe("error"); expect(init.cache).toBe("no-store"); expect(init.method).toBe("POST");
    expect(new TextDecoder().decode(init.body as Uint8Array)).toBe(JSON.stringify({ identity }));
    expect(JSON.stringify(clientReply)).not.toContain(backendBearer); expect(JSON.stringify(clientReply)).not.toContain("synthetic-provider-refresh");
  });

  it("refuses an absent signed-in account before any upstream request", async () => {
    vi.mocked(getToken).mockResolvedValue(null); const response = await candidate("device", { anonymous: true }); expect(response.status).toBe(401); privateResponse(response);
    const authRequest = vi.mocked(getToken).mock.calls[0][0].req; expect(authRequest).toBeInstanceOf(NextRequest);
    expect((authRequest as NextRequest).cookies.has("__Secure-next-auth.session-token")).toBe(false);
    expect(upstream).not.toHaveBeenCalled(); expect(await response.text()).not.toContain(backendBearer);
  });

  it.each<Options>([
    { origin: "https://foreign.example" }, { origin: `${origin}/different-path` }, { csrf: null }, { csrf: "0".repeat(64) },
    { cookie: `__Host-next-auth.csrf-token=${encodeURIComponent(`${csrf}|${"0".repeat(64)}`)}` },
    { headers: { "Sec-Fetch-Site": "cross-site" } }, { headers: { Authorization: "Bearer synthetic-client-selected-token" } },
  ])("rejects foreign candidate origin/CSRF or a client-selected bearer before authenticating", async (options) => {
    const response = await candidate("approve", options); expect(response.status).toBe(403); privateResponse(response);
    expect(getToken).not.toHaveBeenCalled(); expect(upstream).not.toHaveBeenCalled();
  });

  it("forwards device signed bytes unchanged, with only its Chrome extension origin and no account bearer or cookies", async () => {
    const raw = `{ "signature": "synthetic-signature", "payload": {"identity":"${identity}"} }`;
    const response = await device("status", { body: raw }); expect(response.status).toBe(200); privateResponse(response);
    expect(getToken).not.toHaveBeenCalled(); expect(upstream).toHaveBeenCalledTimes(1);
    const [url, init] = upstream.mock.calls[0] as [string, RequestInit]; const headers = new Headers(init.headers);
    expect(url).toBe("https://api.example/api/v1/browser-assistance/device/status"); expect(headers.get("Origin")).toBe(deviceOrigin);
    expect(headers.has("Authorization")).toBe(false); expect(headers.has("Cookie")).toBe(false); expect(init.credentials).toBe("omit"); expect(init.redirect).toBe("error");
    expect(new TextDecoder().decode(init.body as Uint8Array)).toBe(raw);
  });

  it.each<Options>([
    { origin }, { origin: "chrome-extension://short" }, { origin: "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz" },
    { origin: `${deviceOrigin}/popup.html` }, { cookie: "synthetic-account-cookie" }, { cookie: "" },
    { headers: { Authorization: "Bearer synthetic-client-selected-token" } },
  ])("rejects device cookie/account injection or an unrecognized Chrome origin", async (options) => {
    const response = await device("begin", options); expect(response.status).toBe(403); privateResponse(response);
    expect(getToken).not.toHaveBeenCalled(); expect(upstream).not.toHaveBeenCalled();
  });

  it.each(["login", "constructor", "../approve", "approve%2F..%2Flogin", "inspect", "application"])("candidate caller cannot choose device/issuer operation %s", async (operation) => {
    expect((await candidate(operation)).status).toBe(403); expect(upstream).not.toHaveBeenCalled(); expect(getToken).not.toHaveBeenCalled();
  });

  it.each(["approve", "confirm", "revoke", "constructor", "../status"])("device caller cannot choose candidate/issuer operation %s", async (operation) => {
    expect((await device(operation)).status).toBe(403); expect(upstream).not.toHaveBeenCalled(); expect(getToken).not.toHaveBeenCalled();
  });

  it.each<Options>([{ query: "?operation=login" }, { headers: { "Content-Type": "text/plain" } }, { headers: { "Content-Encoding": "gzip" } }])("refuses query/encoding/content-type ambiguity before forwarding", async (options) => {
    expect((await candidate("review", options)).status).toBe(403); expect(upstream).not.toHaveBeenCalled();
  });

  it.each(["[]", "null", "true", '"string"', "{bad"])("refuses a non-object/malformed JSON body %s", async (body) => {
    const response = await candidate("review", { body }); expect([422, 503]).toContain(response.status); privateResponse(response); expect(upstream).not.toHaveBeenCalled();
  });

  it.each([false, true])("bounds candidate/device request bytes without forwarding a truncated body (device=%s)", async (isDevice) => {
    const body = JSON.stringify({ padding: "x".repeat(isDevice ? 65536 : 4096) });
    const response = isDevice ? await device("inspect", { body }) : await candidate("review", { body });
    expect(response.status).toBe(503); privateResponse(response); expect(upstream).not.toHaveBeenCalled();
  });

  it("rejects invalid UTF-8 before decoding or forwarding candidate data", async () => {
    const response = await candidate("review", { body: Uint8Array.from([0xff, 0xfe]) }); expect(response.status).toBe(503); expect(upstream).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404, 409, 410, 413, 422, 429, 503])("preserves a safe %s refusal without retrying or exposing upstream credentials", async (status) => {
    upstream.mockResolvedValue(Response.json({ detail: "synthetic-private-upstream-detail", access_token: "synthetic-private-reply-token" }, { status }));
    const response = await candidate("approve"); expect(response.status).toBe(status); privateResponse(response);
    const body = await response.text(); expect(body).not.toContain("synthetic-private-upstream-detail"); expect(body).not.toContain("synthetic-private-reply-token");
    expect(body).toContain("no automatic retry"); expect(upstream).toHaveBeenCalledTimes(1);
  });

  it.each([302, 500, 502])("maps redirect/unknown failure %s to a private unavailable response without retry", async (status) => {
    upstream.mockResolvedValue(new Response("synthetic-private-refusal", { status })); const response = await device("outcome");
    expect(response.status).toBe(503); privateResponse(response); expect(await response.text()).not.toContain("synthetic-private-refusal"); expect(upstream).toHaveBeenCalledTimes(1);
  });

  it("retains an uncertain network failure and never resends a possibly consumed approval", async () => {
    upstream.mockRejectedValue(new Error("synthetic-lost-begin-reply")); const response = await candidate("approve");
    expect(response.status).toBe(503); privateResponse(response); expect(upstream).toHaveBeenCalledTimes(1); expect(await response.text()).not.toContain("synthetic-lost-begin-reply");
  });

  it.each([[], null, "text", { padding: "x".repeat(65536) }])("refuses primitive or oversized successful upstream responses", async (result) => {
    upstream.mockResolvedValue(Response.json(result)); const response = await candidate("review"); expect(response.status).toBe(503); privateResponse(response); expect(upstream).toHaveBeenCalledTimes(1);
  });

  it.each([
    { ...publicDevice, access_token: "synthetic-private-reply-token" },
    { ...publicDevice, metadata: { refreshToken: "synthetic-private-refresh" } },
    { ...publicDevice, private_key: { value: "synthetic-private-signing-key" } },
    { ...publicDevice, authority_key: { ...publicDevice.authority_key, d: "synthetic-private-jwk-material" } },
  ].flatMap((result) => [{ isDevice: false, result }, { isDevice: true, result }]))("refuses arbitrary authentication/private-key material in successful candidate/device replies", async ({ result, isDevice }) => {
    upstream.mockResolvedValue(Response.json(result)); const response = isDevice ? await device("status") : await candidate("device");
    expect(response.status).toBe(503); privateResponse(response); expect(await response.text()).not.toContain("synthetic-private"); expect(upstream).toHaveBeenCalledTimes(1);
  });
});
