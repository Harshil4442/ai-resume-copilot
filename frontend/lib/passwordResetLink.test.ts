import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const token = "T".repeat(43);
beforeEach(() => { vi.resetModules(); window.history.replaceState({ existing: true }, "", "/"); });
afterEach(() => { window.history.replaceState(null, "", "/"); });

describe("private reset fragment capture", () => {
  it("removes the fragment and permits same-document remount reads without storage or DOM", async () => {
    window.history.replaceState({ existing: true }, "", `/reset-password#token=${token}`);
    const { capturePasswordResetLink, clearPasswordResetToken, readPasswordResetToken } = await import("./passwordResetLink");
    const writes = vi.spyOn(Storage.prototype, "setItem");
    capturePasswordResetLink();
    expect(location.href).not.toContain(token);
    expect(location.hash).toBe("");
    expect(history.state).toEqual({ existing: true });
    expect(document.documentElement.innerHTML).not.toContain(token);
    expect(readPasswordResetToken()).toBe(token);
    expect(readPasswordResetToken()).toBe(token);
    clearPasswordResetToken(token);
    expect(readPasswordResetToken()).toBeNull();
    expect(writes).not.toHaveBeenCalled();
    writes.mockRestore();
  });
  it.each(["token=short", `token=${token}&token=${token}`, `token=${token}&other=x`, `unknown=${token}`, "token=%00".repeat(2)])("strips malformed fragment %s without retaining it", async (fragment) => {
    history.replaceState(null, "", `/reset-password#${fragment}`);
    const { readPasswordResetToken } = await import("./passwordResetLink");
    expect(readPasswordResetToken()).toBeNull();
    expect(location.hash).toBe("");
  });
  it("clears proof on route exit and replaces it on a new capture without stale clearing", async () => {
    const { capturePasswordResetLink, clearPasswordResetToken, readPasswordResetToken } = await import("./passwordResetLink");
    history.replaceState(null, "", `/reset-password#token=${token}`);
    expect(readPasswordResetToken()).toBe(token);
    history.replaceState(null, "", "/forgot-password");
    capturePasswordResetLink();
    expect(readPasswordResetToken()).toBeNull();
    history.replaceState(null, "", `/reset-password#token=${"N".repeat(43)}`);
    expect(readPasswordResetToken()).toBe("N".repeat(43));
    clearPasswordResetToken(token);
    expect(readPasswordResetToken()).toBe("N".repeat(43));
    history.replaceState(null, "", "/reset-password#token=invalid");
    expect(readPasswordResetToken()).toBeNull();
    expect(location.hash).toBe("");
  });
  it("retains only a finite submitted outcome and rejects stale capture-bound updates", async () => {
    const { capturePasswordResetLink, readPasswordResetLink, setPasswordResetOutcome } = await import("./passwordResetLink");
    history.replaceState(null, "", `/reset-password#token=${token}`);
    const first = readPasswordResetLink();
    expect(setPasswordResetOutcome(Symbol(), "submitted")).toBe(false);
    expect(readPasswordResetLink().token).toBe(token);
    expect(setPasswordResetOutcome(first.binding, "submitted")).toBe(true);
    expect(readPasswordResetLink()).toEqual(expect.objectContaining({ token: null, outcome: "submitted" }));
    expect(JSON.stringify(readPasswordResetLink())).not.toContain(token);
    history.replaceState(null, "", `/reset-password#token=${"N".repeat(43)}`);
    expect(readPasswordResetLink()).toEqual(expect.objectContaining({ token: "N".repeat(43), outcome: null }));
    expect(setPasswordResetOutcome(first.binding, "uncertain")).toBe(false);
    history.replaceState(null, "", "/forgot-password"); capturePasswordResetLink();
    expect(readPasswordResetLink()).toEqual(expect.objectContaining({ token: null, outcome: null }));
  });
});
