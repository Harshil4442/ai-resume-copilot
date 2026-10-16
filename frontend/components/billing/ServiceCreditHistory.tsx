"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { History, RefreshCw } from "lucide-react";
import { apiGet } from "../../lib/api";
import {
  serviceCreditHistoryPath,
  serviceOperationLabel,
  serviceOperationStatus,
  type ServiceCreditHistoryPage,
  type ServiceCreditOperation,
  type ServiceOperationFilter,
} from "../../lib/serviceCreditHistory";
import { Button } from "../ui/Button";
import { LoadingBlock } from "../ui/LoadingBlock";

const FILTERS: { value: ServiceOperationFilter; label: string }[] = [
  { value: "all", label: "All services" },
  { value: "job_search", label: "Searches" },
  { value: "job_application", label: "Applications" },
];

function dateLabel(value: string): string {
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function OperationRow({ row }: { row: ServiceCreditOperation }) {
  const pendingPrice = row.state === "reserved" && row.reserved_amount === 0;
  const quantityLabel = row.operation === "job_application" ? "application" : "job";
  return (
    <li className="grid gap-5 py-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-8">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h3 className="font-semibold text-foreground">{serviceOperationLabel(row.operation)}</h3>
          <span className="rounded-full border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground">{serviceOperationStatus(row)}</span>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          {pendingPrice ? "Price reservation pending" : `${row.unit_price} credits per ${quantityLabel}`} · {row.requested_count} requested
        </p>
        <p className="mt-2 break-all text-xs text-muted-foreground">Price version: {row.pricing_version}</p>
        <p className="mt-1 text-xs text-muted-foreground">Started {dateLabel(row.created_at)}{row.settled_at ? ` · Settled ${dateLabel(row.settled_at)}` : ""}</p>
        <p className="mt-1 break-all text-xs text-muted-foreground">Reference: {row.source_id}</p>
      </div>
      <dl className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4 lg:self-center">
        {[
          { label: "Reserved", amount: row.reserved_amount },
          { label: "Held now", amount: row.held_amount },
          { label: "Charged", amount: row.committed_amount },
          { label: "Released", amount: row.released_amount },
        ].map(({ label, amount }) => (
          <div key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className={`mt-1 text-lg font-semibold tabular-nums ${label === "Held now" && amount > 0 ? "text-primary" : "text-foreground"}`}>{amount}<span className="mt-0.5 block text-xs font-normal text-muted-foreground">credits</span></dd>
          </div>
        ))}
      </dl>
    </li>
  );
}

export function ServiceCreditHistory({ refreshKey }: { refreshKey: string }) {
  const [filter, setFilter] = useState<ServiceOperationFilter>("all");
  const [page, setPage] = useState<ServiceCreditHistoryPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  const load = useCallback((cursor?: string | null) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    return apiGet<ServiceCreditHistoryPage>(serviceCreditHistoryPath(filter, cursor), controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      // Older deployments do not yet expose the reservation history contract.
      if (!Array.isArray(response.operations)) throw new Error("Service history is temporarily unavailable.");
      setError(null);
      setPage((previous) => ({
        ...response,
        operations: cursor && previous ? [...previous.operations, ...response.operations] : response.operations,
      }));
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return;
      setError(failure instanceof Error ? failure.message : "Service history could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false);
        setLoadingMore(false);
      }
    });
  }, [filter]);

  const startLoad = (cursor?: string | null) => {
    setError(null);
    if (cursor) setLoadingMore(true);
    else {
      setPage(null);
      setLoading(true);
    }
    void load(cursor);
  };

  useEffect(() => {
    void load();
    return () => requestRef.current?.abort();
  }, [load, refreshKey]);

  return (
    <section className="border-t border-border pt-8" aria-labelledby="service-history-heading">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="data-label">Job service credits</p>
          <h2 id="service-history-heading" className="font-display mt-2 flex items-center gap-2 text-2xl font-normal text-foreground"><History size={20} className="text-primary" aria-hidden="true" /> Service history</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Searches and automatic applications have separate charges. Unused credits return to your available balance; unknown application outcomes can keep credits held.</p>
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={() => startLoad()} disabled={loading || loadingMore}><RefreshCw size={14} aria-hidden="true" />Refresh history</Button>
      </div>

      {page ? <dl className="mt-6 grid gap-4 border-y border-border py-5 sm:grid-cols-2">
        <div><dt className="data-label">Available balance</dt><dd className="mt-2 text-2xl font-semibold tabular-nums text-primary">{page.balance} <span className="text-sm font-normal text-muted-foreground">credits</span></dd></div>
        <div className="sm:border-l sm:border-border sm:pl-6"><dt className="data-label">Currently held</dt><dd className="mt-2 text-2xl font-semibold tabular-nums text-foreground">{page.reserved_credits} <span className="text-sm font-normal text-muted-foreground">credits</span></dd><dd className="mt-1 text-xs text-muted-foreground">Across all searches and applications</dd></div>
      </dl> : null}

      <div className="mt-6 flex flex-wrap gap-2" role="group" aria-label="Filter service history">
        {FILTERS.map(({ value, label }) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => {
          if (value === filter) return;
          setPage(null);
          setError(null);
          setLoading(true);
          setLoadingMore(false);
          setFilter(value);
        }} className={`min-h-10 rounded-full border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${filter === value ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground"}`}>{label}</button>)}
      </div>
      <div aria-busy={loading || loadingMore}>
        {error ? <div className="mt-5 border border-border p-4" role="alert"><p className="text-sm text-foreground">{error}</p><p className="mt-1 text-xs text-muted-foreground">Refresh history to try again. Your credit balance is unchanged by viewing history.</p></div> : null}
        {loading ? <div className="mt-6"><LoadingBlock rows={4} /></div> : page?.operations.length ? <ul className="mt-5 divide-y divide-border border-y border-border">{page.operations.map((row) => <OperationRow key={row.id} row={row} />)}</ul> : !error ? <p className="mt-6 border-y border-border py-7 text-sm text-muted-foreground">No {filter === "job_search" ? "search" : filter === "job_application" ? "application" : "service-credit"} activity yet.</p> : null}
        {!loading && page?.next_cursor ? <div className="mt-5 flex justify-center"><Button type="button" variant="secondary" onClick={() => startLoad(page.next_cursor)} disabled={loadingMore}>{loadingMore ? "Loading older activity…" : "Load older activity"}</Button></div> : null}
      </div>
    </section>
  );
}
