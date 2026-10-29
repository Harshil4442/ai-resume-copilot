import { queryOptions } from "@tanstack/react-query";

import { bootstrapComponent, ownedBootstrapKey, ownedBootstrapOptions } from "./ownedBootstrapClient";

// This key belongs to SessionProviderWrapper's owner-scoped QueryClient. Never
// share the client or an in-flight profile promise across that owner boundary.
export const ownerProfileQueryKey = ownedBootstrapKey;

export function ownerProfileQueryOptions() {
  return queryOptions({
    ...ownedBootstrapOptions(),
    select: (value) => bootstrapComponent(value, "profile"),
  });
}
