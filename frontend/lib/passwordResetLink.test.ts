import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const token = "T".repeat(43);
beforeEach(() => { vi.resetModules(); window.history.replaceState({ existing: true }, "", "/"); });
afterEach(() => { window.history.replaceState(null, "", "/"); });

describe("private reset fragment capture", () => {
  it("removes the fragment before use and transfers the token once without storage or DOM", async () => {
    window.history.replaceState({ existing: true }, "", `/reset-password#token=${token}`);
    const { capturePasswordResetLink, takePasswordResetToken } = await import("./passwordResetLink");
    const writes = vi.spyOn(Storage.prototype, "setItem");
    capturePasswordResetLink();
    expect(location.href).not.toContain(token);
    expect(location.hash).toBe("");
    expect(history.state).toEqual({ existing: true });
    expect(document.documentElement.innerHTML).not.toContain(token);
    expect(takePasswordResetToken()).toBe(token);
    expect(takePasswordResetToken()).toBeNull();
    expect(writes).not.toHaveBeenCalled();
    writes.mockRestore();
  });
  it.each(["token=short", `token=${token}&token=${token}`, `token=${token}&other=x`, `unknown=${token}`, "token=%00".repeat(2)])("strips malformed fragment %s without retaining it", async (fragment) => {
    history.replaceState(null, "", `/reset-password#${fragment}`);
    const { takePasswordResetToken } = await import("./passwordResetLink");
    expect(takePasswordResetToken()).toBeNull();
    expect(location.hash).toBe("");
  });
  it("captures a later new link in the same document without reusing a consumed token", async () => {
    const { takePasswordResetToken } = await import("./passwordResetLink");
    history.replaceState(null, "", `/reset-password#token=${token}`);
    expect(takePasswordResetToken()).toBe(token);
    history.replaceState(null, "", "/forgot-password");
    expect(takePasswordResetToken()).toBeNull();
    history.replaceState(null, "", `/reset-password#token=${"N".repeat(43)}`);
    expect(takePasswordResetToken()).toBe("N".repeat(43));
    expect(location.hash).toBe("");
  });
});
