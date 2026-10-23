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
let clockScope = 0;
beforeEach(() => { vi.restoreAllMocks(); vi.stubEnv("NEXTAUTH_URL", origin); vi.stubEnv("NEXTAUTH_SECRET", secret); vi.stubEnv("BACKEND_URL", "https://backend.example.com"); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", "6e".repeat(32)); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", `fixture_ingress_${++clockScope}`); });
async function request(operation: string, token?: Record<string, unknown>, body?: string, foreign = false) {
  const csrf="a".repeat(64);
  const cookie = (token ? `__Secure-next-auth.session-token=${await encode({token, secret})}; ` : "")
    + `__Host-next-auth.csrf-token=${encodeURIComponent(csrf+"|"+createHash("sha256").update(csrf+secret).digest("hex"))}`;
  return new NextRequest(`${origin}/api/candidate-account/${operation}`, { method: body == null ? "GET" : "POST",
    headers: { cookie, "x-hirewiz-account-csrf":csrf, origin: foreign ? "https://attacker.example.com" : origin, "Content-Type": "application/json" }, body });
}
const params = (operation: string) => ({ params: Promise.resolve({ operation }) });
function recoveryBackendReply(data: unknown, status = 200) {
  const fetch = vi.fn(async (url) => String(url).endsWith("/availability")
    ? Response.json({ fresh_registration: true, legacy_enrollment: true }) : Response.json(data, { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("candidate website projected BFF contracts", () => {
  const legacy = { hirewizUserId: 12, accessToken: "synthetic-private-legacy-bearer", candidateSignInMethod: "google" };
  it("offers explicit Google enrollment from the private sign-in method without manufacturing native context", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const reply = await GET(await request("eligibility", legacy), params("eligibility"));
    expect(await reply.json()).toEqual({status:"GOOGLE_ENROLLMENT_AVAILABLE"}); expect(fetch).not.toHaveBeenCalled();
  });
  it.each(["google-enrollment-challenge", "enroll-google", "enroll-password", "recovery-google-challenge", "recover-google"])("requires same-owner private bearer, CSRF and no existing native account for %s", async (operation) => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await POST(await request(operation, undefined, "{}"), params(operation))).status).toBe(401);
    expect((await POST(await request(operation, retained, "{}"), params(operation))).status).toBe(409);
    expect((await POST(await request(operation, legacy, "{}", true), params(operation))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("forwards exact Google proof bytes with private bearer and an independently signed ingress header", async () => {
    const challenge = {challenge_id:context.account_binding_id, nonce:"n".repeat(43),client_id:"synthetic-google-client",expires_at_ms:Date.now()+300000};
    const fetch = vi.fn(async () => Response.json(challenge)); vi.stubGlobal("fetch", fetch);
    const reply = await POST(await request("google-enrollment-challenge", legacy, "{}"), params("google-enrollment-challenge"));
    expect(await reply.json()).toEqual(challenge);
    expect(fetch).toHaveBeenCalledTimes(2);
    const probe=fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(probe[0]).toBe("https://backend.example.com/api/auth/candidate/v1/availability");
    expect(probe[1].body).toBeUndefined();
    expect(new Headers(probe[1].headers).has("authorization")).toBe(false);
    const sent=fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect(sent[0]).toBe("https://backend.example.com/api/auth/candidate/v1/google-enrollment-challenge");
    expect(new Headers(sent[1].headers).get("authorization")).toBe(`Bearer ${legacy.accessToken}`);
    expect(new Headers(sent[1].headers).get("x-hirewiz-candidate-ingress")).toBeTruthy();
    expect(sent[1].body?.toString()).toBe("{}");
  });
  it("rejects a different candidate's enrollment success and malformed/stale challenge", async () => {
    for (const [operation,data] of [["enroll-google",{status:"ENROLLED",user_id:13}], ["google-enrollment-challenge",{challenge_id:context.account_binding_id,nonce:"n".repeat(43),client_id:"client",expires_at_ms:Date.now()-1}]] as const) {
      vi.stubGlobal("fetch", vi.fn(async () => Response.json(data)));
      const result=await POST(await request(operation,legacy,"{}"),params(operation));
      expect(result.status).toBe(503); expect(await result.json()).toEqual({status:"UNAVAILABLE"});
    }
  });
  it.each(["recovery-google-challenge", "recover-google"])("requires ordinary Google sign-in before %s", async (operation) => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const result = await POST(await request(operation, { ...legacy, candidateSignInMethod: "password" }, "{}"), params(operation));
    expect(result.status).toBe(401); expect(await result.json()).toEqual({ status: "RECOVERY_AUTHENTICATION_FAILED" });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("forwards exact recovery consent/proof and projects same-owner success without a session grant", async () => {
    const body = JSON.stringify({ challenge_id: context.account_binding_id, nonce: "n".repeat(43), id_token: "synthetic-proof",
      password: "synthetic-password", accepted_enrollment: true, accepted_recovery: true });
    const fetch = recoveryBackendReply({ status: "ENROLLED", user_id: 12 });
    const result = await POST(await request("recover-google", legacy, body), params("recover-google"));
    expect(result.status).toBe(200); expect(await result.json()).toEqual({ status: "ENROLLED" });
    const sent = fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect(sent[0]).toBe("https://backend.example.com/api/auth/candidate/v1/recover-google");
    expect(sent[1].body?.toString()).toBe(body);
    expect(new Headers(sent[1].headers).get("authorization")).toBe(`Bearer ${legacy.accessToken}`);
    expect(new Headers(sent[1].headers).get("x-hirewiz-candidate-ingress")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it.each([[401, "RECOVERY_AUTHENTICATION_FAILED"], [403, "RECOVERY_EMAIL_PROOF_REQUIRED"], [409, "RECOVERY_UNAVAILABLE"]] as const)("preserves only exact pre-commit recovery refusal %s", async (status, code) => {
    const fetch = recoveryBackendReply({ status: code }, status);
    const result = await POST(await request("recover-google", legacy, "{}"), params("recover-google"));
    expect(result.status).toBe(status); expect(await result.json()).toEqual({ status: code });
    expect(result.headers.get("cache-control")).toBe("private, no-store");
    expect(fetch.mock.calls.map(([url]) => String(url))).toEqual(["https://backend.example.com/api/auth/candidate/v1/availability", "https://backend.example.com/api/auth/candidate/v1/recover-google"]);
  });
  it.each([
    [401, { detail: "private backend reason" }],
    [401, { status: "RECOVERY_AUTHENTICATION_FAILED", private: "private backend reason" }],
    [503, { status: "RECOVERY_AUTHENTICATION_FAILED" }],
    [200, { status: "ENROLLED", user_id: 13 }],
  ] as const)("keeps malformed or unknown recovery result %s unavailable", async (status, data) => {
    recoveryBackendReply(data, status);
    const result = await POST(await request("recover-google", legacy, "{}"), params("recover-google"));
    expect(result.status).toBe(503); expect(await result.json()).toEqual({ status: "UNAVAILABLE" });
  });
  it("does not leak sensitive validation details from a rejected recovery body", async () => {
    recoveryBackendReply({ detail: [{ input: "private-password" }] }, 422);
    const result = await POST(await request("recover-google", legacy, "{}"), params("recover-google"));
    expect(result.status).toBe(422); expect(await result.json()).toEqual({ status: "RECOVERY_REQUEST_REJECTED" });
  });
  it("decodes private eligibility with the issuer's trimmed secret", async () => {
    vi.stubEnv("NEXTAUTH_SECRET", ` ${secret} `);
    const fetch=vi.fn(async()=>Response.json({status:"ELIGIBLE"}));vi.stubGlobal("fetch",fetch);
    const result=await GET(await request("eligibility",retained),params("eligibility"));
    expect(await result.json()).toEqual({status:"ELIGIBLE"});expect(fetch).toHaveBeenCalledTimes(2);
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
  it.each(["register", "registration-status"])("preserves rate refusal for %s without forwarding backend details", async (operation) => {
    const fetch = vi.fn(async () => Response.json({detail: "synthetic-private-refusal", access_token: "synthetic-private-token"}, {status:429}));
    vi.stubGlobal("fetch", fetch);
    const result = await POST(await request(operation, undefined, "{}"), params(operation));
    expect(result.status).toBe(429);
    expect(await result.json()).toEqual({status:"RATE_LIMITED"});
    expect(result.headers.get("cache-control")).toBe("private, no-store");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
