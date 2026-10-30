import { expect, it } from "vitest";
import { ownedDashboardFixture } from "./fixtures/ownedDashboard";
import { parseOwnedDashboard } from "./ownedDashboardContract";

it("accepts exact advisory components including explicit known partial unavailability", () => {
  const value = ownedDashboardFixture(); expect(parseOwnedDashboard(value)).toEqual(value);
  value.summary = null; value.unavailable = ["summary"];
  expect(parseOwnedDashboard(value)).toEqual(value);
});
it.each([
  { summary: null }, { unavailable: ["summary"] }, { access_token: "synthetic-secret" },
  { schema_version: "unknown" }, { reminders: Array(6).fill({}) }, { opportunities: { total: 0, items: [{}] } },
  { summary: { resume_count: 0, applications_count: 0, average_match_score: Infinity } },
])("rejects malformed, oversized or authority-bearing snapshots %j", (patch) => {
  expect(parseOwnedDashboard({ ...ownedDashboardFixture(), ...patch })).toBeNull();
});
