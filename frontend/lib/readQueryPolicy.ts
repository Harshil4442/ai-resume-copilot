// Capacity refusals need an explicit status check, not another automatic request.
export function isReadCapacityError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error
    && (error.status === 429 || error.status === 503);
}

export function retryRead(failureCount: number, error: unknown): boolean {
  return failureCount < 1 && !isReadCapacityError(error);
}

export const MAX_PROGRESS_READS = 8;

export function progressPollDelay(completedReads: number): number | false {
  if (completedReads >= MAX_PROGRESS_READS) return false;
  return Math.min(30_000, 5_000 * 2 ** Math.min(Math.max(0, completedReads - 1), 3));
}
