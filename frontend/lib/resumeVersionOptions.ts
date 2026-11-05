"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiGet } from "./api";
import type { components } from "./generated/api";
import { retryRead } from "./readQueryPolicy";

type VersionPage = components["schemas"]["ResumeVersionPageResponse"];
class VersionSelectionUnavailable extends Error {}

/** Metadata only, one explicit window; the saved selection is never replaced. */
export function useResumeVersionOptions({ owner, resumeId, currentId, enabled }: {
  owner: string; resumeId: string; currentId: string; enabled: boolean;
}) {
  const scope = JSON.stringify([owner, resumeId, currentId]);
  const [position, setPosition] = useState<{ scope: string; cursor: string | null }>({ scope, cursor: null });
  const cursor = position.scope === scope ? position.cursor : null;
  const query = useQuery({
    queryKey: ["resume-version-options", owner, resumeId, "approved", currentId, cursor],
    queryFn: async ({ signal }) => {
      const page = await apiGet<VersionPage>(`/v1/resume-versions/page?resume_id=${encodeURIComponent(resumeId)}&limit=20&approval_state=approved${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}${currentId ? `&current_id=${encodeURIComponent(currentId)}` : ""}`, signal);
      if (!Array.isArray(page.items) || page.items.some((item) => item.resume_id !== Number(resumeId))
          || (currentId && (page.selected_version?.id !== currentId || page.selected_version.resume_id !== Number(resumeId)))) {
        throw new VersionSelectionUnavailable("The requested resume version could not be verified.");
      }
      return page;
    },
    enabled: enabled && /^[1-9]\d*$/.test(resumeId),
    retry: (count, error) => !(error instanceof VersionSelectionUnavailable)
      && !(typeof error === "object" && error !== null && "status" in error && error.status === 404)
      && retryRead(count, error),
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const items = new Map((query.data?.items ?? []).map((item) => [item.id, item]));
  if (query.data?.selected_version) items.set(query.data.selected_version.id, query.data.selected_version);
  return { ...query, items: Array.from(items.values()), older: cursor !== null, hasMore: Boolean(query.data?.next_cursor),
    more: () => { if (!query.isFetching && query.data?.next_cursor) setPosition({ scope, cursor: query.data.next_cursor }); },
    newest: () => setPosition({ scope, cursor: null }),
  };
}
