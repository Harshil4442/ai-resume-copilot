"use client";

import { useEffect, useState } from "react";

/** Wake only at relevant server deadlines; device wall-clock edits do not extend a quote. */
export function useServerClock(serverTime: string, boundaries: number[]) {
  const [clock, setClock] = useState({ source: serverTime, now: Date.parse(serverTime) });
  const boundaryKey = boundaries.join(",");
  useEffect(() => {
    const base = Date.parse(serverTime), anchor = performance.now();
    const deadlines = boundaryKey.split(",").map(Number).filter((value) => value > base).sort((a, b) => a - b);
    let timer: number | undefined;
    function schedule(now: number) {
      const next = deadlines.find((deadline) => deadline > now);
      if (next !== undefined) timer = window.setTimeout(tick, Math.min(Math.ceil(next - now), 2_147_483_647));
    }
    function tick() {
      const now = base + Math.max(0, performance.now() - anchor);
      setClock({ source: serverTime, now }); schedule(now);
    }
    schedule(base);
    return () => window.clearTimeout(timer);
  }, [serverTime, boundaryKey]);
  return clock.source === serverTime ? clock.now : Date.parse(serverTime);
}
