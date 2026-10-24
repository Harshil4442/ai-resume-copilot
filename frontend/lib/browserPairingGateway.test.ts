// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { boundedPairingBytes, deviceReply, issuePairingCsrf, verifyPairingCsrf } from "./browserPairingGateway";

const key = Buffer.from("b2".repeat(32), "hex");
const context = { candidate_id: 7, account_binding_id: "00000000-0000-4000-8000-000000000001", session_id: "00000000-0000-4000-8000-000000000002" };
describe("pairing CSRF validity and stream resource bounds", () => {
  it("accepts only an unexpired host-cookie/header token bound to the exact retained session", () => {
    const issued = issuePairingCsrf(key, context, 1800000000000);
    expect(verifyPairingCsrf(key, context, issued.cookie, issued.token, 1800000000001)).toBe(true);
    expect(verifyPairingCsrf(key, context, issued.cookie, issued.token, 1800000600000)).toBe(false);
    expect(verifyPairingCsrf(key, { ...context, candidate_id: 8 }, issued.cookie, issued.token, 1800000000001)).toBe(false);
  });
  it("cancels a stream that exceeds its response bound and releases its reader", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new Uint8Array(10)); }, cancel() { cancelled = true; } });
    await expect(boundedPairingBytes(stream, 9)).rejects.toThrow("Pairing size bound exceeded");
    expect(cancelled).toBe(true);
    expect(stream.locked).toBe(false);
  });
  it.each(["timeout", "oversize"])("settles %s without waiting for a cancellation acknowledgement", async (kind) => {
    vi.useFakeTimers();
    try {
      let cancelled = false, outcome = "pending";
      const stream = new ReadableStream<Uint8Array>({
        start(controller) { if (kind === "oversize") controller.enqueue(new Uint8Array(10)); },
        cancel() { cancelled = true; return new Promise<void>(() => undefined); },
      });
      void boundedPairingBytes(stream, 9).then(() => { outcome = "resolved"; }, (error: Error) => { outcome = error.message; });
      await vi.advanceTimersByTimeAsync(kind === "timeout" ? 3000 : 0);
      expect(outcome).toBe(kind === "timeout" ? "Pairing read deadline exceeded" : "Pairing size bound exceeded");
      expect(cancelled).toBe(true);
      expect(stream.locked).toBe(false);
    } finally { vi.useRealTimers(); }
  });
  it("preserves the bound error when cancellation rejects", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array(10)); },
      cancel() { return Promise.reject(new Error("Owned cancellation refusal")); },
    });
    await expect(boundedPairingBytes(stream, 9)).rejects.toThrow("Pairing size bound exceeded");
    expect(stream.locked).toBe(false);
    // Give the rejected cancellation a turn to prove its rejection is handled.
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
});

describe("signed device key inspection replies", () => {
  const reply = { protocol_version: 2, key_sha256: "a".repeat(64), extension_id: "a".repeat(32),
    executor_revision: "companion_test", status: "UNCLAIMED" };
  it.each(["UNCLAIMED", "CLAIMED"])("preserves the finite %s reply without adding identity or authority", (status) => {
    const value = { ...reply, status };
    expect(deviceReply("inspect-key", value)).toBe(value);
  });
  it.each([{ protocol_version: true }, { key_sha256: "invalid" }, { extension_id: "web-origin" },
    { executor_revision: "" }, { status: "UNKNOWN" }, { status: "REQUESTED" }, { owner_id: 7 },
    { device_claim: { private: "synthetic-unexpected-authority" } }])("rejects an unbound or overbroad reply", (changed) => {
    expect(() => deviceReply("inspect-key", { ...reply, ...changed })).toThrow("Invalid pairing reply");
  });
  it("does not accept an unknown mutation receipt as a key-absence result", () => {
    expect(() => deviceReply("inspect-key", { status: "UNKNOWN", operation_id: context.session_id, retry_allowed: false })).toThrow();
  });
});
