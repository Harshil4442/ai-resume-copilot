"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiGet } from "./api";
import { retryAfterMilliseconds } from "./boundedStatusPolling";

export type EmployerJobsRead = <T>(path: string, signal: AbortSignal) => Promise<T>;

export function isEmployerJobsCapacity(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 429 &&
    (error.detail as { detail?: { code?: string } } | null)?.detail?.code === "NATIVE_METADATA_CAPACITY";
}

export function useEmployerJobsReads() {
  const tail = useRef<Promise<void>>(Promise.resolve());
  const loaded = useRef(new Set<string>());
  const blocked = useRef<{ error: ApiError; until: number } | null>(null);
  const [retryAt, setRetryAt] = useState(0);
  const [canRetry, setCanRetry] = useState(false);
  const read: EmployerJobsRead = useCallback(<T,>(path: string, signal: AbortSignal) => {
    const resource = path.split("?", 1)[0];
    async function fetchRead() {
      signal.throwIfAborted();
      if (blocked.current) throw blocked.current.error;
      try {
        const result = await apiGet<T>(path, signal);
        loaded.current.add(resource);
        return result;
      } catch (error) {
        if (isEmployerJobsCapacity(error) && !signal.aborted) {
          const until = Date.now() + (error.retryAfterMs ?? retryAfterMilliseconds(null, 429));
          blocked.current = { error, until };
          setCanRetry(false);
          setRetryAt(until);
        }
        throw error;
      }
    }
    if (loaded.current.has(resource)) return fetchRead();
    const request = tail.current.then(fetchRead);
    tail.current = request.then(() => undefined, () => undefined);
    return request;
  }, []);
  const allowRetry = useCallback(() => {
    if (!blocked.current || Date.now() < blocked.current.until) return false;
    blocked.current = null;
    setRetryAt(0);
    setCanRetry(false);
    return true;
  }, []);

  useEffect(() => {
    if (!retryAt || !Number.isFinite(retryAt)) return;
    let timer: ReturnType<typeof setTimeout>;
    function update() {
      const remaining = retryAt - Date.now();
      if (remaining <= 0) setCanRetry(true);
      else timer = setTimeout(update, Math.min(remaining, 2_147_483_647));
    }
    update();
    return () => clearTimeout(timer);
  }, [retryAt]);

  return { read, paused: retryAt > 0, canRetry, allowRetry };
}
