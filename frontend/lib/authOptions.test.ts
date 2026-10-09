import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JWT } from "next-auth/jwt";
vi.mock("next/headers", () => ({ cookies: vi.fn(async () => ({ get: () => undefined })) }));
import { authOptions } from "./authOptions";

const context = { candidate_id: 12, account_binding_id: "01234567-89ab-cdef-0123-456789abcdef", session_id: "11234567-89ab-cdef-0123-456789abcdef" };
const provider = authOptions.providers.find((item) => item.id === "credentials");
const authorize = (provider as unknown as { options: { authorize: (value: unknown, request: unknown) => Promise<unknown> } }).options.authorize;
const callback = authOptions.callbacks!.jwt!;
beforeEach(() => vi.restoreAllMocks());
const backend = (session?: unknown) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ access_token: "only-synthetic-bearer", user_id: 12, browser_pairing_session: session }))));
const login = () => authorize({ email: "candidate@example.com", password: "synthetic-password" }, {});

describe("retained candidate context remains server-only", () => {
  it("copies validated backend context to JWT and omits it from public session", async () => {
    backend(context);
    const user = await login();
    expect(user).toMatchObject({ browserPairingSession: context });
    const token = await callback({ token: {}, user, account: null } as Parameters<typeof callback>[0]);
    expect(token.browserPairingSession).toEqual(context);
    const session = await authOptions.callbacks!.session!({ session: { user: { name: "Candidate", email: "candidate@example.com" }, expires: "2026-10-10" }, token } as never);
    expect(JSON.stringify(session)).not.toContain("browserPairingSession");
    expect(JSON.stringify(session)).not.toContain(context.session_id);
    expect(JSON.stringify(session)).not.toContain("accessToken");
  });
  it.each([
    { ...context, candidate_id: 13 }, { ...context, candidate_id: true },
    { ...context, account_binding_id: context.account_binding_id.toUpperCase() },
    { ...context, injected: true }, { candidate_id: 12 },
  ])("rejects malformed or cross-account returned context", async (value) => {
    backend(value);
    expect(await login()).toBeNull();
  });
  it("retains legacy login without manufacturing candidate authority", async () => {
    backend();
    const user = await login();
    const token = await callback({ token: { browserPairingSession: context }, user, account: null } as Parameters<typeof callback>[0]);
    expect(token.browserPairingSession).toBeUndefined();
  });
  it("ignores user-controlled session updates", async () => {
    const token: JWT = { hirewizUserId: 12 };
    const result = await callback({ token, trigger: "update", session: { browserPairingSession: context } } as Parameters<typeof callback>[0]);
    expect(result.browserPairingSession).toBeUndefined();
  });
});

it("clears prior password context on verified legacy Google sign-in", async () => {
  backend();
  const result = await callback({ token: { browserPairingSession: context }, user: { id: "12", email: "candidate@example.com" }, account: { provider: "google", id_token: "synthetic-google-id-token" } } as Parameters<typeof callback>[0]);
  expect(result.browserPairingSession).toBeUndefined();
});
