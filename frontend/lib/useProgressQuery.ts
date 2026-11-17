"use client";

import { useQuery, useQueryClient, type QueryFunction, type QueryKey, type QueryState } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";

import { capacityRetryMilliseconds, pageVisible } from "./boundedStatusPolling";
import { MAX_PROGRESS_READS, progressPollDelay, retryRead } from "./readQueryPolicy";

function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

export function usePageVisible() {
  return useSyncExternalStore(subscribeVisibility, () => document.visibilityState !== "hidden", () => true);
}

function retryDeadline(error: unknown, errorUpdatedAt: number): number {
  const wait = capacityRetryMilliseconds(error);
  return wait === null ? 0 : errorUpdatedAt + wait;
}

export function statusCheckAllowed(state: Pick<QueryState, "error" | "errorUpdatedAt" | "fetchStatus"> | undefined, now: number) {
  return state?.fetchStatus === "idle" && retryDeadline(state.error, state.errorUpdatedAt) <= now;
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
  const retryAt = retryDeadline(query.error, query.errorUpdatedAt);
  const [observedAt, setObservedAt] = useState(Date.now);
  const coolingDown = retryAt > observedAt;

  useEffect(() => {
    if (!enabled || !visible || !coolingDown || !Number.isFinite(retryAt)) return;
    let timer: ReturnType<typeof setTimeout>;
    function observeDeadline() {
      const now = Date.now();
      if (now >= retryAt) setObservedAt(now);
      else timer = setTimeout(observeDeadline, Math.min(retryAt - now, 2_147_483_647));
    }
    timer = setTimeout(observeDeadline, Math.min(Math.max(0, retryAt - Date.now()), 2_147_483_647));
    return () => clearTimeout(timer);
  }, [coolingDown, enabled, retryAt, visible]);

  // System refreshes retain query.refetch. A button click checks the latest state
  // of its exact key so remounts and rapid clicks cannot bypass a refusal.
  function checkStatus() {
    if (!enabled || !pageVisible() || !statusCheckAllowed(client.getQueryState(queryKey), Date.now())) return false;
    void query.refetch({ cancelRefetch: false });
    return true;
  }

  return {
    ...query,
    checkStatus,
    statusCheckMessage: coolingDown ? "Status checks are temporarily limited. Try again shortly." : null,
    statusCheckDisabled: !enabled || !visible || query.fetchStatus !== "idle" || coolingDown,
    pollingPaused: enabled && active(query.data) && (query.isError || reads >= MAX_PROGRESS_READS),
  };
}
