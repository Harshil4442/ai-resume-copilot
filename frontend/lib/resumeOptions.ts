"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiGet } from "./api";
import { retryRead } from "./readQueryPolicy";
import type { ResumeListResponse, ResumePageResponse } from "./types";

export const RESUME_PAGE_SIZE = 20;
class ResumeSelectionUnavailable extends Error {}

export type ResumeItem = ResumeListResponse["resumes"][number];

/** Pages advance only on explicit user action; an old selection costs one owned lookup. */
export function useResumeOptions({ selectedId, enabled = true, ownerKey = "current", freshOnMount = false }: {
  selectedId?: string | number | null; enabled?: boolean; ownerKey?: string; freshOnMount?: boolean;
} = {}) {
  const [position, setPosition] = useState<{ owner: string; cursor: string | null }>({ owner: ownerKey, cursor: null });
  const cursor = position.owner === ownerKey ? position.cursor : null;
  const pages = useQuery({
    queryKey: ["resumes", "pages", ownerKey, cursor], enabled,
    queryFn: ({ signal }) => apiGet<ResumePageResponse>(`/resume/page?limit=${RESUME_PAGE_SIZE}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, signal),
    retry: retryRead, refetchOnMount: freshOnMount ? "always" : true, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const loaded = pages.data?.resumes ?? [];
  const identity = Number(selectedId);
  const validIdentity = Number.isSafeInteger(identity) && identity > 0;
  const listedSelection = validIdentity ? loaded.find((item) => item.id === identity) : undefined;
  const needsSelection = enabled && validIdentity && pages.isSuccess && !pages.isFetching && !listedSelection;
  const selection = useResumeSelection({ identity, enabled: needsSelection, ownerKey, freshOnMount });
  const selected = listedSelection ?? (validIdentity && !selection.isError ? selection.data : undefined);
  const unique = new Map(loaded.map((item) => [item.id, item]));
  if (selected && !unique.has(selected.id)) unique.set(selected.id, selected);
  const resumes = Array.from(unique.values());
  return {
    data: pages.data ? { resumes } : undefined, selected,
    isLoading: enabled && (pages.isPending || (needsSelection && selection.isPending)),
    isFetching: pages.isFetching || (needsSelection && selection.isFetching),
    isError: pages.isError || (needsSelection && selection.isError),
    error: pages.error ?? (needsSelection ? selection.error : null),
    hasMore: Boolean(pages.data?.next_cursor), isLoadingMore: cursor !== null && pages.isFetching,
    isOlderPage: cursor !== null,
    loadMore: () => { if (!pages.isFetching && pages.data?.next_cursor) setPosition({ owner: ownerKey, cursor: pages.data.next_cursor }); },
    newest: () => setPosition({ owner: ownerKey, cursor: null }),
    refetch: () => { void pages.refetch(); if (needsSelection) void selection.refetch(); },
  };
}

export type ResumeOptions = ReturnType<typeof useResumeOptions>;

/** One metadata row is independently owner-bound; this never grants source access. */
export function useResumeSelection({ identity, enabled, ownerKey = "current", freshOnMount = false }: {
  identity: number; enabled: boolean; ownerKey?: string; freshOnMount?: boolean;
}) {
  return useQuery({
    queryKey: ["resumes", "selection", ownerKey, identity], enabled,
    queryFn: async ({ signal }) => {
      const page = await apiGet<ResumePageResponse>(`/resume/page?limit=1&current_id=${identity}`, signal);
      if (!page.selected_resume || page.selected_resume.id !== identity) throw new ResumeSelectionUnavailable("Selected resume is unavailable.");
      return page.selected_resume;
    },
    retry: (count, error) => !(error instanceof ResumeSelectionUnavailable) && retryRead(count, error),
    refetchOnMount: freshOnMount ? "always" : true, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
}
