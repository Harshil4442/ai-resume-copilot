import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocked = vi.hoisted(() => ({ backendAuth: vi.fn(), fetch: vi.fn() }));
vi.mock("./candidateAuthServer", () => ({ backendAuth: mocked.backendAuth }));
vi.mock("./browserPairingGateway", () => ({ retainedBrowserSession: () => null }));
vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.stubEnv("NODE_ENV", "test");
  vi.stubEnv("NEXTAUTH_SECRET", "synthetic-unit-test-secret-32-characters");
  vi.stubEnv("GOOGLE_CLIENT_ID", "synthetic-client-id");
  vi.stubEnv("GOOGLE_CLIENT_SECRET", "synthetic-client-secret");
  vi.stubEnv("BACKEND_URL", "http://backend.invalid");
  vi.stubGlobal("fetch", mocked.fetch);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

async function exchange() {
  const { authOptions } = await import("./authOptions");
  const token: Record<string, never> = {};
  const promise = authOptions.callbacks!.jwt!({
    token,
    user: { id: "synthetic-google-user", email: "person@example.invalid" },
    account: {
      provider: "google",
      type: "oauth",
      providerAccountId: "synthetic-google-user",
      id_token: "synthetic-id-token",
    },
  });
  return { token, promise };
}

async function passwordAuthorization() {
  const { authOptions } = await import("./authOptions");
  const provider = authOptions.providers.find((entry) => entry.id === "credentials");
  if (!provider || provider.type !== "credentials") throw new Error("Missing test credentials provider");
  const authorize = provider.options?.authorize ?? provider.authorize;
  return authorize(
    { email: "person@example.invalid", password: "synthetic-password" },
    { body: {}, query: {}, headers: {}, method: "POST" },
  );
}

describe("Google backend failure diagnostics", () => {
  it("directs an explicitly unlinked Google account to its enrolled sign-in method without minting a token", async () => {
    mocked.backendAuth.mockResolvedValue({ ok: false, status: 403, data: {
      detail: "Google account is not explicitly linked; use your enrolled sign-in method",
    } });
    const { token, promise } = await exchange();

    await expect(promise).rejects.toMatchObject({ name: "AccountNotLinkedError" });
    expect(token).toEqual({});
    expect(console.error).not.toHaveBeenCalled();
  });

  it.each([
    { status: 403, data: { detail: "private-response" } },
    { status: 403, data: { detail: "Google account is not explicitly linked; use your enrolled sign-in method", private: "private-response" } },
    { status: 500, data: { detail: "Google account is not explicitly linked; use your enrolled sign-in method" } },
    { status: 401, data: { detail: "Google account is not explicitly linked; use your enrolled sign-in method" } },
  ])("keeps other backend refusals generic and private ($status)", async (reply) => {
    mocked.backendAuth.mockResolvedValue({ ...reply, ok: false });
    const { token, promise } = await exchange();

    await expect(promise).rejects.toThrow("AUTH_UNAVAILABLE");
    expect(token).toEqual({});
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toContain("private-response");
  });

  it("rejects backend 500 without minting a token or logging response or identity data", async () => {
    const sensitive = { detail: "private-sql-error", id_token: "synthetic-id-token", email: "person@example.invalid" };
    mocked.backendAuth.mockResolvedValue({ ok: false, status: 500, data: sensitive });
    mocked.fetch.mockResolvedValue(new Response(JSON.stringify(sensitive), { status: 500 }));
    const { token, promise } = await exchange();

    await expect(promise).rejects.toThrow("AUTH_UNAVAILABLE");
    expect(token).toEqual({});
    expect(console.error).toHaveBeenCalledWith("Google backend sign-in is unavailable.", {
      category: "backend_response", status: 500,
    });
    const logs = JSON.stringify(vi.mocked(console.error).mock.calls);
    expect(logs).not.toContain("private-sql-error");
    expect(logs).not.toContain("synthetic-id-token");
    expect(logs).not.toContain("person@example.invalid");
  });

  it("preserves the network failure category without exposing an exception message", async () => {
    mocked.backendAuth.mockRejectedValue(new Error("private-request-error"));
    mocked.fetch.mockRejectedValue(new Error("private-request-error"));
    const { token, promise } = await exchange();

    await expect(promise).rejects.toThrow("AUTH_UNAVAILABLE");
    expect(token).toEqual({});
    expect(console.error).toHaveBeenCalledWith("Google backend sign-in is unavailable.", { category: "backend_request" });
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toContain("private-request-error");
  });

  it("rejects an invalid successful response and records only category and HTTP status", async () => {
    mocked.backendAuth.mockResolvedValue({ ok: true, status: 200, data: { user_id: "invalid" } });
    mocked.fetch.mockResolvedValue(new Response(JSON.stringify({ user_id: "invalid" }), { status: 200 }));
    const { token, promise } = await exchange();

    await expect(promise).rejects.toThrow("AUTH_UNAVAILABLE");
    expect(token).toEqual({});
    expect(console.error).toHaveBeenCalledWith("Google backend sign-in is unavailable.", {
      category: "backend_contract", status: 200,
    });
  });
  it("distinguishes password backend unavailability from rejected credentials", async () => {
    mocked.backendAuth.mockResolvedValue({ ok: false, status: 503, data: { detail: "private-service-error" } });
    mocked.fetch.mockResolvedValue(new Response(JSON.stringify({ detail: "private-service-error" }), { status: 503 }));

    await expect(passwordAuthorization()).rejects.toThrow("AUTH_UNAVAILABLE");
    const logs = JSON.stringify(vi.mocked(console.error).mock.calls);
    expect(logs).not.toContain("private-service-error");
    expect(logs).not.toContain("synthetic-password");
    expect(logs).not.toContain("person@example.invalid");
  });

  it("retains the rejected-credentials result for password HTTP 401", async () => {
    mocked.backendAuth.mockResolvedValue({ ok: false, status: 401, data: { detail: "Unauthorized" } });
    mocked.fetch.mockResolvedValue(new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 }));

    await expect(passwordAuthorization()).resolves.toBeNull();
    expect(console.error).not.toHaveBeenCalled();
  });

});
