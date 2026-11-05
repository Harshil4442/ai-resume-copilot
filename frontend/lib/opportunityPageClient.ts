import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { apiGet } from "./api";
import { stages } from "./career";
import type { components } from "./generated/api";

export const OPPORTUNITY_PAGE_SIZE = 20;
const stageSchema = z.enum([...stages, "archived"]);
const itemSchema = z.object({
  id: z.string().min(1).max(64),
  title: z.string().max(200),
  company: z.string().max(200),
  location: z.string().max(200),
  stage: stageSchema,
  priority: z.enum(["low", "medium", "high"]),
  next_action: z.string().max(240).nullable(),
  updated_at: z.string().max(64).refine((value) => Number.isFinite(Date.parse(value))),
}).strict();
const countSchema = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const pageSchema = z.object({
  schema_version: z.literal("opportunity-page-v1"),
  items: z.array(itemSchema).max(50),
  total: countSchema,
  total_owned: countSchema,
  active_count: countSchema,
  interview_count: countSchema,
  next_cursor: z.string().min(1).max(1024).nullable(),
}).strict();

export type OpportunityPageItem = components["schemas"]["OpportunityPageItem"];
export type OpportunityPage = components["schemas"]["OpportunityPageResponse"];

/** One bounded window. A cursor is navigation data, never account authority. */
export function opportunityPageOptions({ ownerKey, stage = null, search = "", includeArchived = false,
  cursor = null, limit = OPPORTUNITY_PAGE_SIZE, enabled = true }: {
  ownerKey: string; stage?: string | null; search?: string; includeArchived?: boolean;
  cursor?: string | null; limit?: number; enabled?: boolean;
}) {
  const normalizedSearch = search.trim();
  const normalizedStage = stage || null;
  if (!ownerKey || !Number.isInteger(limit) || limit < 1 || limit > 50 || normalizedSearch.length > 200
    || (normalizedStage !== null && !stageSchema.safeParse(normalizedStage).success)
    || (cursor !== null && (!cursor || cursor.length > 1024))) {
    throw new Error("Opportunity page selection is unavailable.");
  }
  const scope = { limit, stage: normalizedStage, search: normalizedSearch, includeArchived, cursor };
  const params = new URLSearchParams({ limit: String(limit) });
  if (normalizedStage) params.set("stage", normalizedStage);
  if (normalizedSearch) params.set("search", normalizedSearch);
  if (includeArchived) params.set("include_archived", "true");
  if (cursor) params.set("cursor", cursor);
  return queryOptions({
    queryKey: ["opportunities", "pages", ownerKey, scope], enabled,
    queryFn: async ({ signal }): Promise<OpportunityPage> => {
      const raw = await apiGet<unknown>(`/v1/opportunities/page?${params}`, signal);
      const result = pageSchema.safeParse(raw);
      if (!result.success || result.data.items.length > limit
        || new Set(result.data.items.map((row) => row.id)).size !== result.data.items.length
        || (cursor !== null && result.data.next_cursor === cursor)) {
        throw new Error("The opportunity page could not be verified. Please refresh the list.");
      }
      return result.data;
    },
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
