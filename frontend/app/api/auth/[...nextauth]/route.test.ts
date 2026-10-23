// @vitest-environment node
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocked = vi.hoisted(() => ({ handler: vi.fn(), guard: vi.fn() }));
vi.mock("next-auth", () => ({ default: () => mocked.handler }));
vi.mock("../../../../lib/authOptions", () => ({ authOptions: {} }));
vi.mock("../../../../lib/candidateAuthServer", () => ({ guardAuthRequest: mocked.guard }));
import { GET, POST } from "./route";

const privateValue = "PRIVATE_OAUTH_QUERY_COOKIE_BODY_ERROR";
function request(method: string, action = "signin", provider = "google") {
  return new NextRequest(`https://www.hirewizhq.com/api/auth/${action}/${provider}?code=${privateValue}`, {
    method, headers: { cookie: `private=${privateValue}`, authorization: `Bearer ${privateValue}` },
    ...(method === "POST" ? { body: privateValue } : {}),
  });
}
const context = (nextauth: string[]) => ({ params: Promise.resolve({ nextauth }) });

beforeEach(() => {
  vi.restoreAllMocks(); vi.clearAllMocks();
  mocked.guard.mockImplementation(async (value) => ({ request: value }));
});

describe("bounded Google OAuth route timing", () => {
  it.each([["POST", "signin", 200], ["GET", "callback", 302]] as const)(
    "times %s %s without exposing the response URL or any private input", async (method, action, status) => {
      const info = vi.spyOn(console, "info").mockImplementation(() => {});
      vi.spyOn(performance, "now").mockReturnValueOnce(100).mockReturnValueOnce(20350.4);
      const response = new NextResponse(privateValue, { status,
        headers: { location: `https://accounts.google.com/private?state=${privateValue}`, "set-cookie": `private=${privateValue}` } });
      mocked.handler.mockResolvedValueOnce(response);
      const value = request(method, action);
      expect(await (method === "POST" ? POST : GET)(value, context([action, "google"])) ).toBe(response);
      expect(mocked.guard).toHaveBeenCalledExactlyOnceWith(value, action, "google");
      expect(mocked.handler).toHaveBeenCalledTimes(1);
      expect(info).toHaveBeenCalledExactlyOnceWith("hirewiz_oauth_route_timing_v1", {
        action, provider: "google", elapsed_ms: 20250, status,
      });
      expect(JSON.stringify(info.mock.calls)).not.toContain(privateValue);
    },
  );

  it("retains a guard refusal and never invokes NextAuth", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const refusal = NextResponse.json({ error: "SESSION_LOGOUT_REQUIRED" }, { status: 409 });
    mocked.guard.mockResolvedValueOnce({ response: refusal });
    expect(await POST(request("POST"), context(["signin", "google"]))).toBe(refusal);
    expect(mocked.handler).not.toHaveBeenCalled();
    expect(info.mock.calls[0][1]).toMatchObject({ action: "signin", provider: "google", status: 409 });
  });

  it.each(["handler", "guard"])("rethrows the original %s failure without logging its private text", async (stage) => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const error = new Error(privateValue);
    (stage === "guard" ? mocked.guard : mocked.handler).mockRejectedValueOnce(error);
    await expect(GET(request("GET", "callback"), context(["callback", "google"]))).rejects.toBe(error);
    if (stage === "guard") expect(mocked.handler).not.toHaveBeenCalled();
    expect(info.mock.calls[0][1]).toMatchObject({ action: "callback", provider: "google", status: 500 });
    expect(JSON.stringify(info.mock.calls)).not.toContain(privateValue);
  });

  it("does not let a failed diagnostics sink change a successful response", async () => {
    vi.spyOn(console, "info").mockImplementation(() => { throw new Error(privateValue); });
    const response = new NextResponse(null, { status: 302 });
    mocked.handler.mockResolvedValueOnce(response);
    expect(await GET(request("GET", "callback"), context(["callback", "google"]))).toBe(response);
  });

  it.each([
    ["GET", ["session"]], ["POST", ["callback", "credentials"]],
    ["POST", ["signin", "private-provider"]], ["GET", ["signin", "google"]],
    ["POST", ["callback", "google"]], ["POST", ["signin", "google", privateValue]],
  ])("keeps unrelated auth requests out of the timing log: %s %j", async (method, path) => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const response = new NextResponse(null, { status: 200 });
    mocked.handler.mockResolvedValueOnce(response);
    expect(await (method === "POST" ? POST : GET)(request(String(method)), context(path as string[]))).toBe(response);
    expect(info).not.toHaveBeenCalled();
  });

  it.each([[100, 50, 0], [0, Infinity, 0], [NaN, 20, 0], [0, 900000, 600000]])(
    "keeps duration finite and bounded: %s to %s", async (start, end, expected) => {
      const info = vi.spyOn(console, "info").mockImplementation(() => {});
      vi.spyOn(performance, "now").mockReturnValueOnce(start).mockReturnValueOnce(end);
      mocked.handler.mockResolvedValueOnce(new NextResponse(null, { status: 200 }));
      await POST(request("POST"), context(["signin", "google"]));
      expect(info.mock.calls[0][1]).toMatchObject({ elapsed_ms: expected, status: 200 });
    },
  );
});
