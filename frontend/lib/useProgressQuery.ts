"use client";

import { useQuery, useQueryClient, type QueryFunction, type QueryKey } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import { MAX_PROGRESS_READS, progressPollDelay, retryRead } from "./readQueryPolicy";

function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

export function usePageVisible() {
  return useSyncExternalStore(subscribeVisibility, () => document.visibilityState !== "hidden", () => true);
}

export function useProgressQuery<T>({ queryKey, queryFn, enabled = true, active }: {
  queryKey: QueryKey;
  queryFn: QueryFunction<T>;
  enabled?: boolean;
  active: (data: T | undefined) => boolean;
}) {
  const visible = usePageVisible();
  const client = useQueryClient();
  const query = useQuery<T, Error>({
    queryKey, queryFn, enabled, staleTime: 0, retry: retryRead,
    retryOnMount: false, refetchOnMount: (current) => current.state.error === null,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
    refetchIntervalInBackground: false,
    refetchInterval: (current) => !visible || current.state.error || !active(current.state.data)
      ? false : progressPollDelay(current.state.dataUpdateCount),
  });
  const reads = client.getQueryState(queryKey)?.dataUpdateCount ?? 0;
  return {
    ...query,
    pollingPaused: enabled && active(query.data) && (query.isError || reads >= MAX_PROGRESS_READS),
  };
}
