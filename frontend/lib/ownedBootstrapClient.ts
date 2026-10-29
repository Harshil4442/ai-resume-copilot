import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { ApiError, apiGet } from "./api";
import { parseOwnedBootstrap, type BootstrapComponent, type OwnedBootstrap } from "./ownedBootstrapContract";
import type { UserProfile } from "./types";

// Advisory deliverables only. SessionProviderWrapper remounts this cache on
// owner/logout changes; each actual HTTP read performs fresh backend authority.
// The profile prefix retains existing post-mutation invalidations.
export const ownedBootstrapKey = ["profile", "bootstrap-v1"] as const;
export function ownedBootstrapOptions() {
  return queryOptions({ queryKey: ownedBootstrapKey, staleTime: 30_000, retry: false,
    queryFn: async ({ signal }) => {
      const value = parseOwnedBootstrap(await apiGet<unknown>("/auth/bootstrap", signal));
      if (!value) throw new ApiError("Account overview could not be verified.", 503, null);
      return value;
    } });
}
export function bootstrapComponent<K extends BootstrapComponent>(value: OwnedBootstrap, name: K): NonNullable<OwnedBootstrap[K]> {
  const component = value[name];
  if (component === null) throw new ApiError("Account information is temporarily unavailable.", 503, { component: name });
  return component as NonNullable<OwnedBootstrap[K]>;
}
export function featureDecisionsOptions() {
  return queryOptions({ ...ownedBootstrapOptions(), select: (value) => ({ features: bootstrapComponent(value, "features") }) });
}
export async function fetchOwnedProfile(client: QueryClient, force = false) {
  return bootstrapComponent(await client.fetchQuery({ ...ownedBootstrapOptions(), ...(force ? { staleTime: 0 } : {}) }), "profile");
}
export async function fetchOwnedWallet(client: QueryClient, force = false) {
  return bootstrapComponent(await client.fetchQuery({ ...ownedBootstrapOptions(), ...(force ? { staleTime: 0 } : {}) }), "credit_wallet");
}
export function updateOwnedProfile(client: QueryClient, profile: UserProfile) {
  client.setQueryData<OwnedBootstrap>(ownedBootstrapKey, (old) => old ? { ...old, profile,
    unavailable: old.unavailable.filter((name) => name !== "profile") } : undefined);
}
