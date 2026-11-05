import { QueryClient } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";

import { opportunityPageOptions } from "./opportunityPageClient";

const get = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ apiGet: get }));
const clients: QueryClient[] = [];
const client = () => { const value = new QueryClient(); clients.push(value); return value; };
const item = (id = "owned-201") => ({ id, title: "Software Engineer", company: "Owned company", location: "India",
  stage: "saved", priority: "medium", next_action: null, updated_at: "2026-11-05T00:00:00Z" });
const page = (changes = {}) => ({ schema_version: "opportunity-page-v1", items: [item()], total: 201,
  total_owned: 250, active_count: 220, interview_count: 45, next_cursor: "older", ...changes });
afterEach(() => { clients.splice(0).forEach((value) => value.clear()); get.mockReset(); });

it("requests one small metadata window and never follows next_cursor automatically", async () => {
  get.mockResolvedValue(page());
  const data = await client().fetchQuery(opportunityPageOptions({ ownerKey: "owner:one" }));
  expect(get).toHaveBeenCalledExactlyOnceWith("/v1/opportunities/page?limit=20", expect.any(AbortSignal));
  expect(data.items).toHaveLength(1);
  expect(data.total).toBe(201);
  expect(data.active_count).toBe(220);
  expect(JSON.stringify(data)).not.toContain("job_description");
});

it("binds every owner/filter/window input while retaining opportunities prefix invalidation", () => {
  const base = opportunityPageOptions({ ownerKey: "owner:one" }).queryKey;
  for (const change of [{ ownerKey: "owner:two" }, { stage: "interviewing" }, { search: "role" },
    { includeArchived: true }, { cursor: "older" }, { limit: 10 }]) {
    const key = opportunityPageOptions({ ownerKey: "owner:one", ...change }).queryKey;
    expect(key[0]).toBe("opportunities");
    expect(key).not.toEqual(base);
  }
  expect(base).not.toEqual(["opportunities", { limit: 20 }]);
});

it("sends trimmed literal search and encoded cursor without case-folding or wildcard rewriting", async () => {
  get.mockResolvedValue(page({ next_cursor: null }));
  await client().fetchQuery(opportunityPageOptions({ ownerKey: "one", search: "  C%_\\Engineer  ",
    stage: "interviewing", includeArchived: true, cursor: "opaque/+=cursor" }));
  const url = new URL(get.mock.calls[0][0], "https://synthetic.test");
  expect(url.pathname).toBe("/v1/opportunities/page");
  expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "20", search: "C%_\\Engineer",
    stage: "interviewing", include_archived: "true", cursor: "opaque/+=cursor" });
});

it.each([401, 404, 503])("does not retry or fall back from HTTP %i", async (status) => {
  const error = Object.assign(new Error("Synthetic page unavailable"), { status });
  get.mockRejectedValue(error);
  await expect(client().fetchQuery(opportunityPageOptions({ ownerKey: "one" }))).rejects.toBe(error);
  expect(get).toHaveBeenCalledTimes(1);
});

it.each([
  { schema_version: "legacy-list" },
  { items: [item(), item()] },
  { items: Array.from({ length: 21 }, (_, i) => item(String(i))) },
  { total: -1 },
  { total_owned: Number.MAX_SAFE_INTEGER + 1 },
  { items: [{ ...item(), job_description: "A full private body is not a page item" }] },
  { items: [{ ...item(), updated_at: "not-a-date" }] },
])("refuses an invalid or unexpectedly large metadata page without legacy fallback: %j", async (changes) => {
  get.mockResolvedValue(page(changes));
  await expect(client().fetchQuery(opportunityPageOptions({ ownerKey: "one" }))).rejects.toThrow("could not be verified");
  expect(get).toHaveBeenCalledTimes(1);
});

it("rejects a nonadvancing cursor and invalid local request bounds", async () => {
  get.mockResolvedValue(page({ next_cursor: "same-cursor" }));
  await expect(client().fetchQuery(opportunityPageOptions({ ownerKey: "one", cursor: "same-cursor" }))).rejects.toThrow("could not be verified");
  for (const change of [{ limit: 200 }, { stage: "foreign" }, { search: "x".repeat(201) }, { cursor: "x".repeat(1025) }]) {
    expect(() => opportunityPageOptions({ ownerKey: "one", ...change })).toThrow("selection is unavailable");
  }
  expect(get).toHaveBeenCalledTimes(1);
});
