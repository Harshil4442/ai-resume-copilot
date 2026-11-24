"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "../../lib/api";
import { formatCreditDate, parseUnifiedCreditHistory, type UnifiedCreditHistory as HistoryPage } from "../../lib/unifiedCredits";
import { Button } from "../ui/Button";

/** Mounted only while history is open; payment and balance checks do not depend on this read. */
export function UnifiedCreditHistory() {
  const [history, setHistory] = useState<HistoryPage | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(false);
  const request = useRef<AbortController | null>(null);
  const load = useCallback((cursor?: string) => {
    request.current?.abort();
    const work = new AbortController();
    request.current = work;
    void apiGet<unknown>(`/v1/credits/history?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, work.signal).then((raw) => {
      const value = parseUnifiedCreditHistory(raw);
      if (!value) throw new Error("Unverified credit history");
      if (!work.signal.aborted) setHistory((previous) => cursor && previous
        ? { ...value, items: [...previous.items, ...value.items.filter((item) => !previous.items.some((old) => old.id === item.id))] }
        : value);
    }).catch(() => { if (!work.signal.aborted) setError(true); })
      .finally(() => { if (!work.signal.aborted) setBusy(false); });
  }, []);
  const startLoad = (cursor?: string) => {
    setBusy(true); setError(false);
    if (!cursor) setHistory(null);
    load(cursor);
  };
  useEffect(() => {
    load();
    return () => request.current?.abort();
  }, [load]);

  return <div className="mt-4" aria-busy={busy}>
    {error ? <div role="status" className="space-y-3 text-sm"><p>Credit history could not be loaded. Your balance has not been changed.</p><Button variant="secondary" disabled={busy} onClick={() => startLoad()}>Retry credit history</Button></div> : null}
    {history ? history.items.length ? <ul className="divide-y divide-border">{history.items.map((event) => <li key={event.id} className="grid gap-2 py-4 text-sm sm:grid-cols-[1fr_auto_auto]"><span>{event.event_type.replaceAll("_", " ")} · {formatCreditDate(event.created_at)}</span><span>{event.amount > 0 ? "+" : ""}{event.amount} credits</span><span>Balance {event.balance_after}</span></li>)}</ul>
      : <p className="text-sm text-muted-foreground">No credit activity yet.</p>
      : busy ? <p role="status" className="text-sm text-muted-foreground">Loading credit history…</p> : null}
    {history?.next_cursor ? <Button variant="secondary" className="mt-4" disabled={busy} onClick={() => startLoad(history.next_cursor!)}>Load older activity</Button> : null}
  </div>;
}
