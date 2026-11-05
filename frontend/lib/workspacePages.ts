"use client";

import { type QueryClient, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiGet } from "./api";
import type { Workspace } from "./career";
import type { ResumeOptions } from "./resumeOptions";
import { retryRead } from "./readQueryPolicy";
import type { ResumePageResponse } from "./types";

export type WorkspaceTab = "overview" | "resume" | "learning" | "interview" | "outcome" | "activity";
export type WorkspaceCollection = keyof Workspace["pages"];
export type MetadataPage<T> = { items: T[]; next_cursor?: string | null };
export const WORKSPACE_PAGE_SIZE = 20;
export const workspaceKey = (owner: string, opportunity: string) => ["workspace", owner, opportunity] as const;
export const workspaceConnectionKey = (owner: string, opportunity: string) => ["workspace-source-connection", owner, opportunity] as const;
export type PendingWorkspaceConnection = { resumeId: number; title: string };

/** A connection hint cannot supply histories, evidence approval or source authority. */
export async function resetWorkspaceAfterSourceConnection(client: QueryClient, owner: string, opportunity: string, pending: PendingWorkspaceConnection) {
  const key = workspaceKey(owner, opportunity);
  await client.cancelQueries({ queryKey: key });
  client.removeQueries({ queryKey: key });
  client.setQueryData(workspaceConnectionKey(owner, opportunity), pending);
}

/** One compact, tab-bound RPC. Bodies and continuation pages are explicit reads. */
export function useWorkspace(owner: string, opportunity: string, tab: WorkspaceTab, enabled: boolean, selectedVersion?: string) {
  return useQuery({
    queryKey: [...workspaceKey(owner, opportunity), "tab", tab, selectedVersion || null], enabled,
    queryFn: ({ signal }) => apiGet<Workspace>(`/v1/opportunities/${encodeURIComponent(opportunity)}/workspace?tab=${tab}&limit=${WORKSPACE_PAGE_SIZE}${selectedVersion ? `&selected_version_id=${encodeURIComponent(selectedVersion)}` : ""}`, signal),
    retry: retryRead, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
}

/** A bounded window, not an accumulated history. No automatic page traversal. */
export function useWorkspacePage<T>({ owner, opportunity, tab, source, collection, seed, enabled, refreshing, refresh }: {
  owner: string; opportunity: string; tab: WorkspaceTab; source: number | null | undefined;
  collection: WorkspaceCollection; seed: MetadataPage<T> | null | undefined; enabled: boolean;
  refreshing: boolean; refresh: () => unknown;
}) {
  const scope = JSON.stringify([owner, opportunity, tab, source, collection]);
  const [position, setPosition] = useState<{ scope: string; cursor: string | null }>({ scope, cursor: null });
  const cursor = position.scope === scope ? position.cursor : null;
  const query = useQuery({
    queryKey: [...workspaceKey(owner, opportunity), "page", tab, source, collection, cursor],
    queryFn: ({ signal }) => apiGet<MetadataPage<T>>(`/v1/opportunities/${encodeURIComponent(opportunity)}/workspace/${collection}?limit=${WORKSPACE_PAGE_SIZE}&cursor=${encodeURIComponent(cursor!)}`, signal),
    enabled: enabled && cursor !== null, retry: retryRead,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const page = cursor === null ? seed : query.data;
  const fetching = refreshing || (cursor !== null && query.isFetching);
  return {
    items: page?.items ?? [], hasMore: Boolean(page?.next_cursor), older: cursor !== null,
    isFetching: fetching, isError: cursor !== null && query.isError,
    isLoading: enabled && (cursor === null ? !seed : query.isPending),
    more: () => { if (!fetching && page?.next_cursor) setPosition({ scope, cursor: page.next_cursor }); },
    newest: () => { setPosition({ scope, cursor: null }); refresh(); },
    retry: () => { if (cursor === null) refresh(); else void query.refetch(); },
  };
}

/** The bootstrap already includes both the first resume page and exact current selection. */
export function useWorkspaceResumes({ owner, opportunity, seed, loading, fetching, error, refresh }: {
  owner: string; opportunity: string; seed: Workspace["resumes"] | undefined;
  loading: boolean; fetching: boolean; error: unknown; refresh: () => unknown;
}): ResumeOptions {
  const source = seed?.selected_resume?.id;
  const scope = JSON.stringify([owner, opportunity, source]);
  const [position, setPosition] = useState<{ scope: string; cursor: string | null }>({ scope, cursor: null });
  const cursor = position.scope === scope ? position.cursor : null;
  const continuation = useQuery({
    queryKey: [...workspaceKey(owner, opportunity), "resume-options", source, cursor],
    queryFn: ({ signal }) => apiGet<ResumePageResponse>(`/resume/page?limit=${WORKSPACE_PAGE_SIZE}&cursor=${encodeURIComponent(cursor!)}${source ? `&current_id=${source}` : ""}`, signal),
    enabled: Boolean(seed) && cursor !== null, retry: retryRead,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const page = cursor === null ? seed : continuation.data;
  const selected = seed?.selected_resume ? { ...seed.selected_resume, source_format: seed.selected_resume.source_format ?? null } : undefined;
  const unique = new Map((page?.resumes ?? []).map((resume) => [resume.id, { ...resume, source_format: resume.source_format ?? null }]));
  if (selected) unique.set(selected.id, selected);
  return {
    data: page ? { resumes: Array.from(unique.values()) } : undefined, selected,
    isLoading: loading || (cursor !== null && continuation.isPending),
    isFetching: fetching || (cursor !== null && continuation.isFetching),
    isError: Boolean(error) || (cursor !== null && continuation.isError),
    error: error ? error : (cursor !== null && continuation.error ? continuation.error : null),
    hasMore: Boolean(page?.next_cursor), isLoadingMore: cursor !== null && continuation.isFetching,
    isOlderPage: cursor !== null,
    loadMore: () => { if (!fetching && !continuation.isFetching && page?.next_cursor) setPosition({ scope, cursor: page.next_cursor }); },
    newest: () => { setPosition({ scope, cursor: null }); refresh(); },
    refetch: () => { if (cursor === null) refresh(); else void continuation.refetch(); },
  };
}
