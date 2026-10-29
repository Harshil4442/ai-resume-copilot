import { isReadCapacityError, MAX_PROGRESS_READS, progressPollDelay } from "./readQueryPolicy";

export type StatusReadBudget = { automaticReads: number; notBefore: number };
export const newStatusReadBudget = (): StatusReadBudget => ({ automaticReads: 0, notBefore: 0 });
export const pageVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

export async function waitUntilPageVisible(signal: AbortSignal): Promise<boolean> {
  signal.throwIfAborted();
  if (pageVisible()) return false;
  await new Promise<void>((resolve, reject) => {
    function clean() { document.removeEventListener("visibilitychange", visible); signal.removeEventListener("abort", abort); }
    function visible() { if (pageVisible()) { clean(); resolve(); } }
    function abort() { clean(); reject(new DOMException("Status check stopped", "AbortError")); }
    document.addEventListener("visibilitychange", visible);
    signal.addEventListener("abort", abort, { once: true });
  });
  return true;
}

export function retryAfterMilliseconds(value: string | null, status: number, now = Date.now()): number {
  const fallback = status === 429 ? 60_000 : 30_000;
  const header = value?.trim() ?? "";
  let delay: number | null = null;
  if (/^[0-9]+$/.test(header)) {
    const seconds = Number(header);
    // An unrepresentably distant valid minimum pauses this flow indefinitely;
    // it must never become a short fallback or an overflowing setTimeout.
    delay = seconds > Number.MAX_SAFE_INTEGER / 1000 ? Infinity : seconds * 1000;
  }
  else if (/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(header)) delay = Date.parse(header) - now;
  return delay !== null && !Number.isNaN(delay) && delay > 0 ? delay : fallback;
}

export function abortableStatusDelay(signal: AbortSignal, milliseconds: number): Promise<boolean> {
  signal.throwIfAborted();
  if (!pageVisible()) return Promise.resolve(false);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(true), milliseconds);
    function clean() { clearTimeout(timer); signal.removeEventListener("abort", abort); if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visibility); }
    function finish(value: boolean) { clean(); resolve(value); }
    function abort() { clean(); reject(new DOMException("Status check stopped", "AbortError")); }
    function visibility() { if (!pageVisible()) finish(false); }
    signal.addEventListener("abort", abort, { once: true });
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibility);
  });
}

/** Automatic checks stop on hidden/capacity/uncertain outcomes. Manual is one GET. */
export async function boundedStatusChecks<T>({ budget, signal, automatic, read, active }: {
  budget: StatusReadBudget; signal: AbortSignal; automatic: boolean;
  read: (signal: AbortSignal) => Promise<T>; active: (value: T) => boolean;
}): Promise<"settled" | "pending" | "paused"> {
  while (true) {
    signal.throwIfAborted();
    if (!pageVisible() || (automatic && budget.automaticReads >= MAX_PROGRESS_READS)) return "paused";
    if (Date.now() < budget.notBefore) throw new Error("Please wait for the requested retry interval before checking payment status again.");
    if (automatic) budget.automaticReads += 1;
    let value: T;
    try { value = await read(signal); }
    catch (error) {
      if (isReadCapacityError(error)) {
        const item = error as { status: number; retryAfterMs?: number | null };
        const delay = typeof item.retryAfterMs === "number" && !Number.isNaN(item.retryAfterMs) && item.retryAfterMs > 0
          ? item.retryAfterMs : retryAfterMilliseconds(null, item.status);
        budget.notBefore = Math.max(budget.notBefore, Date.now() + delay);
      }
      throw error;
    }
    signal.throwIfAborted();
    if (!active(value)) return "settled";
    if (!automatic) return "pending";
    const wait = progressPollDelay(budget.automaticReads);
    if (wait === false || !await abortableStatusDelay(signal, wait)) return "paused";
  }
}
