import { queryOptions } from "@tanstack/react-query";

import { apiGet } from "./api";
import type { UserProfile } from "./types";

// This key belongs to SessionProviderWrapper's owner-scoped QueryClient. Never
// share the client or an in-flight profile promise across that owner boundary.
export const ownerProfileQueryKey = ["profile"] as const;

export function ownerProfileQueryOptions() {
  return queryOptions({
    queryKey: ownerProfileQueryKey,
    queryFn: ({ signal }) => apiGet<UserProfile>("/auth/profile", signal),
    staleTime: 30_000,
  });
}
