import { queryOptions } from "@tanstack/react-query";
import { ApiError, apiGet } from "./api";
import { ownedBootstrapKey } from "./ownedBootstrapClient";
import { parseOwnedBootstrap } from "./ownedBootstrapContract";
import { parseOwnedDashboard, type OwnedDashboard } from "./ownedDashboardContract";

export const ownedDashboardKey = [...ownedBootstrapKey, "dashboard-v1"] as const;
function unavailable(): never { throw new ApiError("Dashboard information could not be verified.", 503, null); }
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }

async function legacyDashboard(signal: AbortSignal): Promise<OwnedDashboard> {
  const bootstrap = parseOwnedBootstrap(await apiGet<unknown>("/auth/bootstrap", signal));
  if (!bootstrap) return unavailable();
  // Only an absent new route permits compatibility reads. Any auth/capacity or
  // malformed result fails this complete fallback; no authority error is partial.
  const summary = await apiGet<unknown>("/analytics/summary", signal);
  if (!object(summary)) return unavailable();
  const workspace = bootstrap.features?.career_workspace?.enabled === true;
  let opportunities: unknown = null, reminders: unknown = null;
  if (workspace) {
    const [roles, followups] = await Promise.all([
      apiGet<unknown>("/v1/opportunities?limit=8", signal), apiGet<unknown>("/v1/reminders?status=scheduled", signal),
    ]);
    if (!object(roles) || !Array.isArray(roles.items) || !Array.isArray(followups)) return unavailable();
    opportunities = { total: roles.total, items: roles.items.map((row) => {
      if (!object(row)) return null;
      return { id: row.id, title: row.title, company: row.company, stage: row.stage, next_action: row.next_action };
    }) };
    reminders = followups.slice(0, 5).map((row) => {
      if (!object(row)) return null;
      return { id: row.id, message: row.message, due_at: row.due_at };
    });
  }
  const quality = summary.resume_quality;
  if (!object(quality)) return unavailable();
  return parseOwnedDashboard({ schema_version: "owned-dashboard-v1", bootstrap,
    summary: { resume_count: summary.resume_count, applications_count: summary.applications_count, average_match_score: summary.average_match_score,
      resume_quality: { evidenced_skills: quality.evidenced_skills, verification_rate: quality.verification_rate } },
    opportunities, reminders, unavailable: workspace ? [] : ["opportunities", "reminders"] }) || unavailable();
}

export function ownedDashboardOptions() {
  return queryOptions({ queryKey: ownedDashboardKey, staleTime: 30_000, retry: false,
    queryFn: async ({ signal }) => {
      let value: unknown;
      try { value = await apiGet<unknown>("/auth/dashboard", signal); }
      catch (error) {
        if (error instanceof ApiError && error.status === 404) return legacyDashboard(signal);
        throw error;
      }
      return parseOwnedDashboard(value) || unavailable();
    } });
}
