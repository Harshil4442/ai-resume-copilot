import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  captureNotificationUnsubscribeLink,
  clearNotificationUnsubscribeLink,
  readNotificationUnsubscribeLink,
  setNotificationUnsubscribeOutcome,
} from "./notificationUnsubscribeLink";

const TOKEN = `hwdu1.eyJ2ZXJzaW9uIjoxfQ.${"A".repeat(43)}`;
const NEXT_TOKEN = `hwdu1.eyJ2ZXJzaW9uIjoxfQ.${"B".repeat(43)}`;

beforeEach(() => {
  history.replaceState(null, "", "/");
  clearNotificationUnsubscribeLink();
});
afterEach(() => {
  vi.restoreAllMocks();
  clearNotificationUnsubscribeLink();
  history.replaceState(null, "", "/");
});

describe("unsubscribe fragment custody", () => {
  it.each(["/notifications/unsubscribe", "/notifications/unsubscribe/"])("captures only this document's proof and strips it at %s", (path) => {
    const state = { syntheticNavigation: "preserved" };
    history.replaceState(state, "", `${path}#token=${TOKEN}`);
    captureNotificationUnsubscribeLink();
    expect(location.pathname).toBe(path);
    expect(location.hash).toBe("");
    expect(location.search).toBe("");
    expect(history.state).toEqual(state);
    expect(readNotificationUnsubscribeLink()).toEqual({ token: TOKEN, outcome: null, binding: expect.any(Symbol) });
  });

  it.each(["/profile", "/notifications/unsubscribe-example", "/notifications/unsubscribe/extra",
    "/api/backend/public/notifications/unsubscribe"])("never adopts a proof on another path %s", (path) => {
    history.replaceState(null, "", `${path}#token=${TOKEN}`);
    expect(readNotificationUnsubscribeLink().token).toBeNull();
  });

  it.each([`?token=${TOKEN}`, `?token=${TOKEN}#token=${TOKEN}`, `?unexpected=1#token=${TOKEN}`])("rejects and strips query-bearing link %#", (suffix) => {
    history.replaceState(null, "", `/notifications/unsubscribe${suffix}`);
    expect(readNotificationUnsubscribeLink().token).toBeNull();
    expect(location.href).not.toContain(TOKEN);
    expect(location.hash).toBe("");
    expect(location.search).toBe("");
  });

  it.each([`#token=${TOKEN}&token=${NEXT_TOKEN}`, `#token=${TOKEN}&enabled=true`, `#other=${TOKEN}`,
    "#token=", "#token=not-a-proof", `#token=other.eyJ2ZXJzaW9uIjoxfQ.${"A".repeat(43)}`,
    `#token=hwdu1..${"A".repeat(43)}`, `#token=hwdu1.payload.${"A".repeat(42)}`,
    `#token=hwdu1.payload.${"A".repeat(44)}`, `#token=hwdu1.payload=.${"A".repeat(43)}`,
    `#token=hwdu1.payload.${"A".repeat(42)}%2B`, `#token=${"X".repeat(4201)}`])("strips duplicate, malformed or oversized fragment %#", (fragment) => {
    history.replaceState(null, "", `/notifications/unsubscribe${fragment}`);
    expect(readNotificationUnsubscribeLink().token).toBeNull();
    expect(location.hash).toBe("");
  });

  it("bounds the proof value itself independently of the fragment bound", () => {
    const maximum = `hwdu1.${"A".repeat(4046)}.${"B".repeat(43)}`;
    expect(maximum).toHaveLength(4096);
    history.replaceState(null, "", `/notifications/unsubscribe#token=${maximum}`);
    expect(readNotificationUnsubscribeLink().token).toBe(maximum);
    history.replaceState(null, "", `/notifications/unsubscribe#token=${maximum}A`);
    expect(readNotificationUnsubscribeLink().token).toBeNull();
    expect(location.hash).toBe("");
  });

  it("does not read or write browser storage during capture, consumption or remount reads", () => {
    const get = vi.spyOn(Storage.prototype, "getItem");
    const set = vi.spyOn(Storage.prototype, "setItem");
    const remove = vi.spyOn(Storage.prototype, "removeItem");
    history.replaceState(null, "", `/notifications/unsubscribe#token=${TOKEN}`);
    const retained = readNotificationUnsubscribeLink();
    expect(setNotificationUnsubscribeOutcome(retained.binding, "submitted")).toBe(true);
    expect(readNotificationUnsubscribeLink().token).toBeNull();
    expect(get).not.toHaveBeenCalled();
    expect(set).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
  });

  it("invalidates the old binding on route changes and cannot consume a newly captured proof with it", () => {
    history.replaceState(null, "", `/notifications/unsubscribe#token=${TOKEN}`);
    const old = readNotificationUnsubscribeLink();
    history.replaceState(null, "", "/profile");
    const cleared = readNotificationUnsubscribeLink();
    expect(cleared.token).toBeNull();
    expect(cleared.binding).not.toBe(old.binding);
    expect(setNotificationUnsubscribeOutcome(old.binding, "complete")).toBe(false);
    history.replaceState(null, "", `/notifications/unsubscribe#token=${NEXT_TOKEN}`);
    const current = readNotificationUnsubscribeLink();
    expect(current.token).toBe(NEXT_TOKEN);
    expect(current.binding).not.toBe(old.binding);
    expect(setNotificationUnsubscribeOutcome(old.binding, "invalid")).toBe(false);
    expect(readNotificationUnsubscribeLink().token).toBe(NEXT_TOKEN);
  });

  it.each(["submitted", "complete", "invalid", "uncertain"] as const)("consumes the proof on %s and retains only that outcome across same-document remount reads", (outcome) => {
    history.replaceState(null, "", `/notifications/unsubscribe#token=${TOKEN}`);
    const initial = readNotificationUnsubscribeLink();
    expect(setNotificationUnsubscribeOutcome(initial.binding, outcome)).toBe(true);
    captureNotificationUnsubscribeLink();
    expect(readNotificationUnsubscribeLink()).toEqual({ token: null, outcome, binding: initial.binding });
    expect(readNotificationUnsubscribeLink().token).toBeNull();
    expect(location.hash).toBe("");
  });
});
