import { beforeEach, describe, expect, it, vi } from "vitest";
import { candidateIngressHeaders } from "./candidateIngressServer";
const origin = "https://www.hirewiz.example";
const path = "/api/auth/candidate/v1/login";
const id = "01234567-89ab-cdef-0123-456789abcdef";
const now = 2000000000000;
const input = { method: "POST", headers: { authorization: "Bearer synthetic-private-only" }, body: '{"password":"synthetic-private-only"}' };
beforeEach(() => {
  vi.unstubAllEnvs();
  vi.stubEnv("NEXTAUTH_URL", origin);
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", "6e".repeat(32));
  vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "fixture_ingress_v1");
});
describe("website server private ingress signer", () => {
  it("creates a bounded exact safe payload without bearer or credential input", () => {
    const header = candidateIngressHeaders(path, input, now, id).get("x-hirewiz-candidate-ingress")!;
    const [wire, signature] = header.split(".");
    const data = JSON.parse(Buffer.from(wire, "base64url").toString());
    expect(data).toMatchObject({ version: 1, issuer: "hirewiz_candidate_bff_v1", origin, method: "POST", path,
      request_id: id, issued_at_ms: now, expires_at_ms: now + 5000 });
    expect(Buffer.from(signature, "base64url")).toHaveLength(32);
    expect(data.body_commitment).toMatch(/^[a-f0-9]{64}$/);
    expect(data.authorization_commitment).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.stringify(data)).not.toContain("synthetic-private-only");
  });
  it("binds UUID, body and separately server-held bearer", () => {
    const first = candidateIngressHeaders(path, input, now, id).get("x-hirewiz-candidate-ingress");
    expect(candidateIngressHeaders(path, { ...input, body: input.body + " " }, now, id).get("x-hirewiz-candidate-ingress")).not.toBe(first);
    expect(candidateIngressHeaders(path, { ...input, headers: { authorization: "Bearer replacement" } }, now, id).get("x-hirewiz-candidate-ingress")).not.toBe(first);
    expect(candidateIngressHeaders(path, input, now, "11234567-89ab-cdef-0123-456789abcdef").get("x-hirewiz-candidate-ingress")).not.toBe(first);
  });
  it("keeps ordinary legacy auth available without private provision but refuses native", () => {
    vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_SECRET", ""); vi.stubEnv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "");
    expect(candidateIngressHeaders("/api/auth/login", input).has("x-hirewiz-candidate-ingress")).toBe(false);
    expect(() => candidateIngressHeaders(path, input)).toThrow("AUTH_UNAVAILABLE");
  });
  it.each(["BROWSER_PAIRING_GATEWAY_SECRET", "NEXTAUTH_SECRET"])("refuses reuse of %s custody", (name) => {
    vi.stubEnv(name, "6e".repeat(32));
    expect(() => candidateIngressHeaders(path, input)).toThrow("AUTH_UNAVAILABLE");
  });
  it.each(["/api/auth/candidate/v1/login/", "/api/auth/candidate/v1/%6cogin", "/api/auth/candidate/v1/unknown"])("refuses unsupported private path %s", (value) => {
    expect(() => candidateIngressHeaders(value, input)).toThrow("AUTH_UNAVAILABLE");
  });
  it("removes any caller-supplied assertion and refuses unbounded sensitive inputs", () => {
    expect(candidateIngressHeaders("/api/auth/profile", { headers: { "x-hirewiz-candidate-ingress": "untrusted" } }).has("x-hirewiz-candidate-ingress")).toBe(false);
    expect(() => candidateIngressHeaders(path, { ...input, body: "x".repeat(8193) })).toThrow("AUTH_UNAVAILABLE");
    expect(() => candidateIngressHeaders(path, { ...input, headers: { authorization: "x".repeat(8193) } })).toThrow("AUTH_UNAVAILABLE");
  });
});
