import type { UserProfile } from "./types";
import { parseCreditAvailability, type CreditAvailability } from "./unifiedServiceQuotes";
import { parseUnifiedCreditWallet, type UnifiedCreditWallet } from "./unifiedCredits";

export const OWNED_BOOTSTRAP_SCHEMA = "owned-bootstrap-v1";
export const BOOTSTRAP_COMPONENTS = ["profile", "features", "credit_availability", "credit_wallet"] as const;
export type BootstrapComponent = typeof BOOTSTRAP_COMPONENTS[number];
export type FeatureDecisions = { features: Record<string, { enabled: boolean }> };
export type OwnedBootstrap = {
  schema_version: typeof OWNED_BOOTSTRAP_SCHEMA;
  profile: UserProfile | null;
  features: FeatureDecisions["features"] | null;
  credit_availability: CreditAvailability | null;
  credit_wallet: UnifiedCreditWallet | null;
  unavailable: BootstrapComponent[];
};
const PROFILE_FIELDS = ["full_name", "headline", "phone", "location", "linkedin", "github", "portfolio", "target_role",
  "preferred_job_type", "preferred_location", "years_experience", "bio", "skills", "education", "certifications",
  "email", "profile_completeness", "missing_fields", "tier", "ai_credits", "premium_until"];
const TEXT_FIELDS = PROFILE_FIELDS.filter((key) => !["years_experience", "skills", "profile_completeness", "missing_fields", "ai_credits"].includes(key));
const PRIVATE_FIELDS = new Set(["access_token", "accesstoken", "refresh_token", "browser_pairing_session", "browserpairingsession",
  "candidate_lifetime", "candidatelogoutrequest", "password_hash"]);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, fields: readonly string[]) => Object.keys(value).length === fields.length && fields.every((key) => Object.hasOwn(value, key));
const integer = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const strings = (value: unknown, maximum = 5000): value is string[] => Array.isArray(value) && value.length <= maximum && value.every((entry) => typeof entry === "string");

export function bootstrapHasNoAuthority(value: unknown): boolean {
  const pending = [value]; let nodes = 0;
  while (pending.length) {
    if (++nodes > 100000) return false;
    const entry = pending.pop();
    if (!entry || typeof entry !== "object") continue;
    for (const [key, child] of Object.entries(entry)) { if (PRIVATE_FIELDS.has(key.toLowerCase())) return false; pending.push(child); }
  }
  return true;
}

export function parseOwnedBootstrap(value: unknown): OwnedBootstrap | null {
  if (!object(value) || !exact(value, ["schema_version", ...BOOTSTRAP_COMPONENTS, "unavailable"])
    || value.schema_version !== OWNED_BOOTSTRAP_SCHEMA || !strings(value.unavailable, 4)
    || !value.unavailable.every((name) => BOOTSTRAP_COMPONENTS.includes(name as BootstrapComponent))
    || new Set(value.unavailable).size !== value.unavailable.length || !bootstrapHasNoAuthority(value)) return null;
  for (const name of BOOTSTRAP_COMPONENTS) if ((value[name] === null) !== value.unavailable.includes(name)) return null;
  const profile = value.profile;
  if (profile !== null && (!object(profile) || !exact(profile, PROFILE_FIELDS)
    || typeof profile.email !== "string" || !profile.email.includes("@") || !["free", "premium"].includes(String(profile.tier))
    || !integer(profile.ai_credits) || !integer(profile.profile_completeness) || Number(profile.profile_completeness) > 100
    || typeof profile.years_experience !== "number" || !Number.isFinite(profile.years_experience)
    || !strings(profile.skills) || !strings(profile.missing_fields, 15)
    || !TEXT_FIELDS.every((key) => profile[key] === null || typeof profile[key] === "string"))) return null;
  const features = value.features;
  if (features !== null && (!object(features) || Object.keys(features).length > 64
    || !Object.entries(features).every(([key, row]) => object(row) && exact(row, ["key", "enabled", "variant", "rollout_percent", "bucket", "reason"])
      && row.key === key && /^[a-z][a-z0-9_]{0,63}$/.test(key) && typeof row.enabled === "boolean"
      && row.variant === (row.enabled ? "on" : "off") && integer(row.rollout_percent) && Number(row.rollout_percent) <= 100
      && integer(row.bucket) && Number(row.bucket) < 100 && ["kill_switch", "internal", "invited", "percentage"].includes(String(row.reason))))) return null;
  const availability = value.credit_availability === null ? null : parseCreditAvailability(value.credit_availability);
  const wallet = value.credit_wallet === null ? null : parseUnifiedCreditWallet(value.credit_wallet);
  if (value.credit_availability !== null && !availability || value.credit_wallet !== null && !wallet) return null;
  return { schema_version: OWNED_BOOTSTRAP_SCHEMA, profile: profile as UserProfile | null,
    features: features as FeatureDecisions["features"] | null, credit_availability: availability,
    credit_wallet: wallet, unavailable: value.unavailable as BootstrapComponent[] };
}
