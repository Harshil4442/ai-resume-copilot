import { afterEach, describe, expect, it } from "vitest";
import { capturePrivacyErasureLink, clearPrivacyErasureLink, readPrivacyErasureLink, setPrivacyErasureOutcome } from "./privacyErasureLink";

const token = "E".repeat(43);
afterEach(() => { clearPrivacyErasureLink(); history.replaceState(null, "", "/"); });

describe("private erasure fragment", () => {
  it("strips proof before rendering and keeps it only in memory for an explicit same-token action", () => {
    history.replaceState(null, "", `/privacy-erasure/confirm#token=${token}`);
    capturePrivacyErasureLink();
    const link = readPrivacyErasureLink();
    expect(location.hash).toBe(""); expect(link.token).toBe(token);
    expect(sessionStorage.length).toBe(0); expect(document.body.innerHTML).not.toContain(token);
    expect(setPrivacyErasureOutcome(link.binding, "submitted")).toBe(true);
    expect(readPrivacyErasureLink()).toEqual({ ...link, outcome: "submitted" });
    setPrivacyErasureOutcome(link.binding, "uncertain");
    expect(readPrivacyErasureLink()).toEqual({ ...link, outcome: "uncertain" });
    setPrivacyErasureOutcome(link.binding, "complete");
    expect(readPrivacyErasureLink()).toEqual({ token: null, outcome: "complete", binding: link.binding });
  });
  it.each(["token=short", `token=${token}&token=${token}`, `token=${token}&other=x`, `other=${token}`])("strips malformed fragment %s", (fragment) => {
    history.replaceState(null, "", `/privacy-erasure/confirm#${fragment}`);
    capturePrivacyErasureLink();
    expect(location.hash).toBe(""); expect(readPrivacyErasureLink().token).toBeNull();
  });
  it("rejects and strips query tokens and never shares password-reset proof", () => {
    history.replaceState(null, "", `/privacy-erasure/confirm?token=${token}#token=${token}`);
    capturePrivacyErasureLink();
    expect(location.search).toBe(""); expect(location.hash).toBe("");
    expect(readPrivacyErasureLink().token).toBeNull();
    history.replaceState(null, "", `/reset-password#token=${token}`);
    expect(readPrivacyErasureLink().token).toBeNull();
  });
  it("a late response cannot clear a newer link", () => {
    history.replaceState(null, "", `/privacy-erasure/confirm#token=${token}`);
    const previous = readPrivacyErasureLink();
    history.replaceState(null, "", `/privacy-erasure/confirm#token=${"N".repeat(43)}`);
    const next = readPrivacyErasureLink();
    expect(setPrivacyErasureOutcome(previous.binding, "complete")).toBe(false);
    expect(readPrivacyErasureLink()).toEqual(next);
  });
});
