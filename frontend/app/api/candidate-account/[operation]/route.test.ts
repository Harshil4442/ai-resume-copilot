// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { GET, POST } from "./route";

const origin = "https://candidate.example.com", secret = "synthetic-candidate-bff-cookie-secret";
const context = { candidate_id: 12, account_binding_id: "01234567-89ab-cdef-0123-456789abcdef",
  session_id: "11234567-89ab-cdef-0123-456789abcdef" };
const retained = { hirewizUserId: 12, accessToken: "synthetic-private-server-bearer", browserPairingSession: context };
beforeEach(() => { vi.restoreAllMocks(); vi.stubEnv("NEXTAUTH_URL", origin); vi.stubEnv("NEXTAUTH_SECRET", secret); vi.stubEnv("BACKEND_URL", "https://backend.example.com"); });
async function request(operation: string, token?: Record<string, unknown>, body?: string, foreign = false) {
  const csrf="a".repeat(64);
  const cookie = (token ? `__Secure-next-auth.session-token=${await encode({token, secret})}; ` : "")
    + `__Host-next-auth.csrf-token=${encodeURIComponent(csrf+"|"+createHash("sha256").update(csrf+secret).digest("hex"))}`;
  return new NextRequest(`${origin}/api/candidate-account/${operation}`, { method: body == null ? "GET" : "POST",
    headers: { cookie, "x-hirewiz-account-csrf":csrf, origin: foreign ? "https://attacker.example.com" : origin, "Content-Type": "application/json" }, body });
}
const params = (operation: string) => ({ params: Promise.resolve({ operation }) });

describe("candidate website projected BFF contracts", () => {
  it("decodes private eligibility with the issuer's trimmed secret", async () => {
    vi.stubEnv("NEXTAUTH_SECRET", ` ${secret} `);
    const fetch=vi.fn(async()=>Response.json({status:"ELIGIBLE"}));vi.stubGlobal("fetch",fetch);
    const result=await GET(await request("eligibility",retained),params("eligibility"));
    expect(await result.json()).toEqual({status:"ELIGIBLE"});expect(fetch).toHaveBeenCalledTimes(1);
  });
  it.each([[undefined, "PASSWORD_SIGN_IN_REQUIRED"], [{hirewizUserId: 12}, "LEGACY_ENROLLMENT_UNAVAILABLE"],
    [{browserPairingSession: {candidate_id: 12}}, "UNAVAILABLE"]] as const)("projects absent, legacy and malformed private sessions without native calls", async (token, status) => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const result = await GET(await request("eligibility", token), params("eligibility"));
    expect(result.status).toBe(200); expect(await result.json()).toEqual({ status }); expect(fetch).not.toHaveBeenCalled();
  });
  it.each([[200, {status: "ELIGIBLE"}, "ELIGIBLE"], [401, {detail: "private-error"}, "PASSWORD_SIGN_IN_REQUIRED"],
    [503, {detail: "private-error"}, "UNAVAILABLE"], [200, {status: "ELIGIBLE", session_id: "private"}, "UNAVAILABLE"]] as const)("checks actual backend eligibility but exposes only fixed status", async (status, data, expected) => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(data, {status})));
    const result = await GET(await request("eligibility", retained), params("eligibility"));
    expect(result.status).toBe(200); expect(await result.json()).toEqual({status: expected});
    expect(result.headers.get("cache-control")).toBe("private, no-store");
  });
  it("does not permit native signup to replace a retained identity or create another account", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const result = await POST(await request("register", retained, "{}"), params("register"));
    expect(result.status).toBe(409); expect(await result.json()).toEqual({status:"SESSION_LOGOUT_REQUIRED"}); expect(fetch).not.toHaveBeenCalled();
  });
  it("preserves ordinary registration when authority is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "private" }, {status:503})));
    const result = await GET(await request("availability"), params("availability"));
    expect(await result.json()).toEqual({fresh_registration:false, legacy_enrollment:false});
  });
  it.each(["register", "registration-status"])("rejects foreign origin before sensitive body/backend calls", async (operation) => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const result = await POST(await request(operation, undefined, '{"password":"private"}', true), params(operation));
    expect(result.status).toBe(403); expect(await result.text()).not.toContain("private"); expect(fetch).not.toHaveBeenCalled();
  });
  it("bounds candidate credential body before backend forwarding", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const result = await POST(await request("register", undefined, "x".repeat(8193)), params("register"));
    expect(result.status).toBe(503); expect(fetch).not.toHaveBeenCalled();
  });
  it("projects native signup success without identity or lifetime claims", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({user_id:12,status:"ENROLLED"})));
    const result = await POST(await request("register", undefined, "{}"), params("register"));
    expect(await result.json()).toEqual({status:"ENROLLED"});
  });
});
