import { queryOptions } from "@tanstack/react-query";
import { apiGet } from "./api";
import { parseUnifiedCreditCatalog, parseUnifiedCreditWallet } from "./unifiedCredits";
import { parseCreditAvailability } from "./unifiedServiceQuotes";

// These keys share only SessionProviderWrapper's owner-scoped QueryClient.
export const creditAvailabilityKey = ["unified-credits", "availability"] as const;
export const creditWalletKey = ["unified-credits", "wallet"] as const;
export function creditAvailabilityOptions() {
  return queryOptions({ queryKey: creditAvailabilityKey, staleTime: 30_000, retry: false,
    queryFn: async ({ signal }) => parseCreditAvailability(await apiGet<unknown>("/v1/credits/availability", signal)) });
}
export function creditWalletOptions() {
  return queryOptions({ queryKey: creditWalletKey, staleTime: 30_000, retry: false,
    queryFn: async ({ signal }) => parseUnifiedCreditWallet(await apiGet<unknown>("/v1/credits/wallet", signal)) });
}
export function unifiedCatalogOptions() {
  return queryOptions({ queryKey: ["unified-credits", "catalog"], staleTime: 0, retry: false,
    queryFn: async ({ signal }) => parseUnifiedCreditCatalog(await apiGet<unknown>("/v1/credits/catalog", signal)) });
}
