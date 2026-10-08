import { describe, expect, it } from "vitest";

import { batchEligibility, batchItemMatches } from "./employerJobs";
import { batchApplication, batchCatalog, quotedBatch } from "./fixtures/employerBatch";

describe("exact batch package contracts", () => {
  it("uses saved per-job prices even when the catalog apply price changes", () => {
    const app = batchApplication(1);
    expect(app.credit_cost).not.toBe(batchCatalog.apply_credits_per_job);
    expect(batchEligibility(app, batchCatalog)).toBeNull();
    expect(batchItemMatches(quotedBatch([app]).items[0], app)).toBe(true);
  });
  it.each(["manual", "manual_handoff"])("excludes %s employer routes", (mode) => {
    expect(batchEligibility({ ...batchApplication(1), application_mode: mode }, batchCatalog)).toContain("manual handoff");
  });
  it.each(["queued", "submitting", "unknown", "confirmed", "cancelled"] as const)("never selects an already %s application", (status) => {
    expect(batchEligibility({ ...batchApplication(1), status }, batchCatalog)).not.toBeNull();
  });
  it("fails closed for legacy quotes, missing files, incomplete forms, disabled service, and expired policy", () => {
    const app = batchApplication(1);
    for (const changed of [{ ...app, admission_snapshot: null }, { ...app, pricing_snapshot: null }, { ...app, artifact: null }, { ...app, missing_fields: ["privacy"] }, { ...app, batch_id: "other_batch" }, { ...app, admission_snapshot: { ...app.admission_snapshot!, expires_at: "2020-01-01T00:00:00Z" } }]) expect(batchEligibility(changed, batchCatalog)).not.toBeNull();
    expect(batchEligibility(app, { ...batchCatalog, auto_submit_enabled: false })).not.toBeNull();
    expect(batchEligibility(app, { ...batchCatalog, admission_limits: undefined })).not.toBeNull();
  });
  it("rejects a stale digest, changed canonical opening, price, policy or authorized action", () => {
    const app = batchApplication(1), item = quotedBatch([app]).items[0];
    for (const changed of [{ ...app, package_digest: "f".repeat(64) }, { ...app, opening_key: "new_opening" }, { ...app, employer_key: "other_owner" }, { ...app, credit_cost: 8 }, { ...app, pricing_snapshot: { unit_price: 7, pricing_version: "new_price" } }, { ...app, admission_snapshot: { ...app.admission_snapshot!, policy_fingerprint: "new_policy" } }]) expect(batchItemMatches(item, changed)).toBe(false);
    expect(batchItemMatches({ ...item, allowed_actions: ["fill", "upload"] }, app)).toBe(false);
    expect(batchItemMatches(item, undefined)).toBe(false);
  });
});
