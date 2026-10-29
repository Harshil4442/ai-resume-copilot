import { queryOptions } from "@tanstack/react-query";
import { apiGet } from "./api";
import { parseUnifiedCreditCatalog } from "./unifiedCredits";
import { bootstrapComponent, ownedBootstrapKey, ownedBootstrapOptions } from "./ownedBootstrapClient";

// These keys share only SessionProviderWrapper's owner-scoped QueryClient.
export const creditAvailabilityKey = ownedBootstrapKey;
export const creditWalletKey = ownedBootstrapKey;
export function creditAvailabilityOptions() {
  return queryOptions({ ...ownedBootstrapOptions(), select: (value) => bootstrapComponent(value, "credit_availability") });
}
export function creditWalletOptions() {
  return queryOptions({ ...ownedBootstrapOptions(), select: (value) => bootstrapComponent(value, "credit_wallet") });
}
export function unifiedCatalogOptions() {
  return queryOptions({ queryKey: ["unified-credits", "catalog"], staleTime: 0, retry: false,
    queryFn: async ({ signal }) => parseUnifiedCreditCatalog(await apiGet<unknown>("/v1/credits/catalog", signal)) });
}
