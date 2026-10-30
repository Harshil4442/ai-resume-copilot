import type { components } from "./generated/api";
import { bootstrapHasNoAuthority, parseOwnedBootstrap, type OwnedBootstrap } from "./ownedBootstrapContract";

export type OwnedDashboard = Omit<components["schemas"]["OwnedDashboardView"], "bootstrap"> & { bootstrap: OwnedBootstrap };
export const DASHBOARD_COMPONENTS = ["summary", "opportunities", "reminders"] as const;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[]) => Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const integer = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const text = (value: unknown, bound: number) => typeof value === "string" && value.length <= bound;
const stages = ["saved", "evaluating", "preparing", "applied", "interviewing", "offer", "rejected", "withdrawn", "archived"];

export function parseOwnedDashboard(value: unknown): OwnedDashboard | null {
  if (!object(value) || !exact(value, ["schema_version", "bootstrap", ...DASHBOARD_COMPONENTS, "unavailable"])
    || value.schema_version !== "owned-dashboard-v1" || !Array.isArray(value.unavailable) || value.unavailable.length > 3
    || value.unavailable.some((key) => !DASHBOARD_COMPONENTS.includes(key)) || new Set(value.unavailable).size !== value.unavailable.length
    || !bootstrapHasNoAuthority(value)) return null;
  const bootstrap = parseOwnedBootstrap(value.bootstrap);
  if (!bootstrap) return null;
  for (const key of DASHBOARD_COMPONENTS) if ((value[key] === null) !== value.unavailable.includes(key)) return null;
  const summary = value.summary, opportunities = value.opportunities, reminders = value.reminders;
  if (summary !== null && (!object(summary) || !exact(summary, ["resume_count", "applications_count", "average_match_score", "resume_quality"])
    || !integer(summary.resume_count) || !integer(summary.applications_count)
    || typeof summary.average_match_score !== "number" || !Number.isFinite(summary.average_match_score)
    || !object(summary.resume_quality) || !exact(summary.resume_quality, ["evidenced_skills", "verification_rate"])
    || !integer(summary.resume_quality.evidenced_skills) || !integer(summary.resume_quality.verification_rate)
    || Number(summary.resume_quality.verification_rate) > 100)) return null;
  if (opportunities !== null && (!object(opportunities) || !exact(opportunities, ["items", "total"])
    || !integer(opportunities.total) || !Array.isArray(opportunities.items) || opportunities.items.length > 8
    || Number(opportunities.total) < opportunities.items.length
    || opportunities.items.some((row) => !object(row) || !exact(row, ["id", "title", "company", "stage", "next_action"])
      || !text(row.id, 64) || !text(row.title, 200) || !text(row.company, 200)
      || !stages.includes(String(row.stage)) || (row.next_action !== null && !text(row.next_action, 240))))) return null;
  if (reminders !== null && (!Array.isArray(reminders) || reminders.length > 5
    || reminders.some((row) => !object(row) || !exact(row, ["id", "message", "due_at"])
      || !text(row.id, 64) || !text(row.message, 240) || !text(row.due_at, 64) || !Number.isFinite(Date.parse(String(row.due_at)))))) return null;
  return { ...value, bootstrap } as OwnedDashboard;
}
