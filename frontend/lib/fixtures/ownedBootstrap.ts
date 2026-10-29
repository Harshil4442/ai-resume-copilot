import type { UserProfile } from "../types";
import type { OwnedBootstrap } from "../ownedBootstrapContract";

export function ownedBootstrapFixture({ profile = {}, features = { career_workspace: true }, availability, wallet }: {
  profile?: Partial<UserProfile>;
  features?: Record<string, boolean>;
  availability?: unknown;
  wallet?: unknown;
} = {}): OwnedBootstrap {
  const defaults: UserProfile = { email: "owner@example.com", profile_completeness: 0, missing_fields: [],
    full_name: null, headline: null, phone: null, location: null, linkedin: null, github: null, portfolio: null,
    target_role: null, preferred_job_type: null, preferred_location: null, years_experience: 0, bio: null,
    skills: [], education: null, certifications: null, tier: "free", ai_credits: 7, premium_until: null };
  const rows = Object.fromEntries(Object.entries(features).map(([key, enabled]) => [key,
    { key, enabled, variant: enabled ? "on" : "off", rollout_percent: enabled ? 100 : 0, bucket: 0, reason: enabled ? "percentage" : "kill_switch" }]));
  const projectedProfile = { ...defaults, ...profile };
  // The real profile response never exports this separate /auth/me field.
  delete projectedProfile.job_service_credits;
  const value: OwnedBootstrap = { schema_version: "owned-bootstrap-v1", profile: projectedProfile, features: rows,
    credit_availability: availability === null ? null : { catalog_enabled: false, execution_enabled: false,
      supported_operations: [], free_core_operations: [], free_core_limits: {}, ...(availability as object | undefined) },
    credit_wallet: wallet === undefined ? { schema_version: "unified-credits-v1", server_time: "2026-10-29T00:00:00Z",
      available_credits: 0, reserved_credits: 0, debt_credits: 0, lots: [] } : wallet as OwnedBootstrap["credit_wallet"],
    unavailable: [] };
  if (value.credit_availability === null) value.unavailable.push("credit_availability");
  if (value.credit_wallet === null) value.unavailable.push("credit_wallet");
  return value;
}
