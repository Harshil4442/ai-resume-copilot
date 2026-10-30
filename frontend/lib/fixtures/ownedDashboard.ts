import type { OwnedDashboard } from "../ownedDashboardContract";
import type { OwnedBootstrap } from "../ownedBootstrapContract";
import { ownedBootstrapFixture } from "./ownedBootstrap";

export function ownedDashboardFixture(bootstrap: OwnedBootstrap = ownedBootstrapFixture()): OwnedDashboard {
  const workspace = bootstrap.features?.career_workspace?.enabled === true;
  return { schema_version: "owned-dashboard-v1", bootstrap,
    summary: { resume_count: 1, applications_count: 1, average_match_score: 75,
      resume_quality: { evidenced_skills: 2, verification_rate: 50 } },
    opportunities: workspace ? { total: 1, items: [{ id: "op-one", title: "Owned role", company: "Company", stage: "saved", next_action: null }] } : null,
    reminders: workspace ? [{ id: "rem-one", message: "Owned follow-up", due_at: "2026-10-30T12:00:00Z" }] : null,
    unavailable: workspace ? [] : ["opportunities", "reminders"] };
}
