import { queryOptions } from "@tanstack/react-query";
import { apiGet } from "./api";
import type { OpportunityList } from "./career";

export function opportunitiesOptions({ limit, stage = null, search = "", offset = 0, includeArchived = false }: {
  limit: number; stage?: string | null; search?: string; offset?: number; includeArchived?: boolean;
}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (stage) params.set("stage", stage);
  if (search) params.set("search", search);
  if (offset) params.set("offset", String(offset));
  if (includeArchived) params.set("include_archived", "true");
  return queryOptions({ queryKey: ["opportunities", { limit, stage, search, offset, includeArchived }],
    queryFn: ({ signal }) => apiGet<OpportunityList>(`/v1/opportunities?${params}`, signal) });
}
