"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, apiGet, apiPostJson } from "../lib/api";
import { readSavedSearchQuery, type EmployerSearchSummary, type SearchResult } from "../lib/employerJobs";
import { emptyPreferenceDraft, preferenceDraft, preferenceInput } from "../lib/searchPreferences";
import type { ResumeListResponse } from "../lib/types";
import { parseDiscoveryBatch, parseDiscoveryQuote, type DiscoveryBatch, type DiscoveryCreate, type DiscoveryRequest } from "../lib/unifiedDiscovery";
import { creditWalletKey } from "../lib/unifiedCreditClient";
import { useProgressQuery } from "../lib/useProgressQuery";
import type { UnifiedServiceQuote } from "../lib/unifiedServiceQuotes";
import { EmployerSearchPreferences } from "./EmployerSearchPreferences";
import { SavedResultCard } from "./EmployerSavedSearches";
import { ServiceChargeReview } from "./billing/ServiceChargeReview";
import { Button } from "./ui/Button";
import { LoadingBlock } from "./ui/LoadingBlock";

const base = "/v1/credits";
const batchKey = (id: string | null) => ["unified-credits", "discovery", id] as const;
type Review = { quote: UnifiedServiceQuote; input: DiscoveryRequest; identity: string };
function readBatch(value: unknown) {
  const batch = parseDiscoveryBatch(value);
  if (!batch) throw new Error("The search status could not be verified. Check the original request before starting another batch.");
  return batch;
}
function definitelyUnstarted(error: unknown) {
  if (!(error instanceof ApiError)) return false;
  const detail = (error.detail as { detail?: { code?: string; run_created?: boolean } } | null)?.detail;
  return detail?.code === "credit_service_quote_rejected" && detail.run_created === false;
}

/** Mount by selected saved-search ID; parent locks history while an intent is unresolved. */
export function UnifiedDiscoveryPanel({ resumes, enabled, initialSearch, preparingApplication, onPrepare, onResume, onLockChange }: {
  resumes: ResumeListResponse["resumes"]; enabled: boolean; initialSearch?: EmployerSearchSummary | null;
  preparingApplication: boolean; onPrepare: (postingId: string, resumeId: number) => void;
  onResume: (resumeId: string) => void; onLockChange: (locked: boolean) => void;
}) {
  const saved = readSavedSearchQuery(initialSearch?.query);
  const [resumeId, setResumeId] = useState(saved ? String(saved.resume_id) : "");
  const [role, setRole] = useState(saved?.role ?? ""), [location, setLocation] = useState(saved?.location ?? "");
  const [remote, setRemote] = useState(saved?.remote_only ?? false), [days, setDays] = useState(saved?.published_within_days ? String(saved.published_within_days) : "");
  const [preferences, setPreferences] = useState(() => saved ? preferenceDraft(saved.preferences) : { ...emptyPreferenceDraft, employment: [...emptyPreferenceDraft.employment] });
  const [review, setReview] = useState<Review | null>(null), [pending, setPending] = useState<DiscoveryCreate | null>(null);
  const [batchId, setBatchId] = useState<string | null>(initialSearch?.id ?? null);
  const client = useQueryClient(), choice = preferenceInput(preferences);
  const input: DiscoveryRequest = { resume_id: Number(resumeId), role: role.trim(), location: location.trim(), desired_count: 5,
    remote_only: remote, published_within_days: days ? Number(days) : null, excluded_employers: saved?.excluded_employers ?? [], preferences: choice.preferences };
  const identity = JSON.stringify(input);
  const progress = useProgressQuery<DiscoveryBatch>({ queryKey: batchKey(batchId), enabled: Boolean(batchId),
    queryFn: async ({ signal }) => readBatch(await apiGet<unknown>(`${base}/discovery-batches/${encodeURIComponent(batchId!)}`, signal)),
    active: (batch) => batch?.status === "preparing" });
  const refresh = useCallback(() => {
    window.dispatchEvent(new Event("refresh_unified_credits"));
    void client.invalidateQueries({ queryKey: creditWalletKey });
    void client.invalidateQueries({ queryKey: ["employer-jobs", "search-summaries"] });
  }, [client]);
  const quote = useMutation({ retry: false, mutationFn: async (request: DiscoveryRequest) => {
    const parsed = parseDiscoveryQuote(await apiPostJson<unknown>(`${base}/discovery-quotes`, request, { "Idempotency-Key": crypto.randomUUID() }), 5);
    if (!parsed) throw new Error("A verified one-credit search quote is unavailable. No batch has been approved.");
    return { quote: parsed, input: request, identity: JSON.stringify(request) };
  }, onSuccess: setReview });
  const create = useMutation({ retry: false, mutationFn: async (request: DiscoveryCreate) => readBatch(await apiPostJson<unknown>(`${base}/discovery-batches`, request)),
    onSuccess: (batch, request) => { client.setQueryData(batchKey(batch.id), batch); setBatchId(batch.id); setPending(null); setReview(null); onResume(String(request.resume_id)); refresh(); },
    onError: (error) => { if (definitelyUnstarted(error)) { setPending(null); setReview(null); } refresh(); } });
  const cancel = useMutation({ retry: false, mutationFn: async () => readBatch(await apiPostJson<unknown>(`${base}/discovery-batches/${encodeURIComponent(batchId!)}/cancel`, {})),
    onSuccess: (batch) => { client.setQueryData(batchKey(batch.id), batch); refresh(); } });
  // A failed status read leaves the original batch unresolved, even if the last known state was missing.
  const unresolved = Boolean(batchId && (!progress.data || progress.data.status === "preparing"));
  const locked = quote.isPending || create.isPending || pending !== null || unresolved;
  useEffect(() => { onLockChange(locked); return () => onLockChange(false); }, [locked, onLockChange]);
  const resultStatus = progress.data?.status;
  useEffect(() => { if (resultStatus && resultStatus !== "preparing") refresh(); }, [resultStatus, refresh]);
  const valid = Boolean(resumeId && role.trim().length >= 2 && !choice.error);
  const selectedResume = resumes.find((resume) => String(resume.id) === resumeId);
  const canReview = enabled && valid && !locked;
  function requestQuote() { if (canReview) { setReview(null); create.reset(); quote.mutate(input); } }
  const batch = progress.data;

  return <section className="mt-8 grid min-w-0 gap-8 xl:grid-cols-[340px_minmax(0,1fr)]" aria-label="Reviewed employer discovery">
    <form className="surface min-w-0 self-start p-5 sm:p-6" onSubmit={(event) => { event.preventDefault(); requestQuote(); }}>
      <h2 className="font-display text-2xl">Your search</h2>
      <div className="mt-5 grid gap-4">
        <label className="grid gap-2 text-sm font-semibold">Resume<select className="field-control min-w-0" value={resumeId} disabled={locked} required onChange={(event) => { setResumeId(event.target.value); onResume(event.target.value); }}><option value="">Choose a resume</option>{resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename} · #{resume.id}</option>)}</select></label>
        <Link className="text-xs font-semibold text-primary underline underline-offset-4" href="/resume">Upload another resume</Link>
        <label className="grid gap-2 text-sm font-semibold">Target role<input className="field-control" value={role} minLength={2} maxLength={200} disabled={locked} required onChange={(event) => setRole(event.target.value)} placeholder="e.g. Backend Engineer" /></label>
        <label className="grid gap-2 text-sm font-semibold">Location<input className="field-control" value={location} maxLength={200} disabled={locked} onChange={(event) => setLocation(event.target.value)} placeholder="City or country" /></label>
        <label className="grid gap-2 text-sm font-semibold">Published/released within<select className="field-control" value={days} disabled={locked} onChange={(event) => setDays(event.target.value)}><option value="">Any date</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option></select></label>
        <p className="text-xs leading-5 text-muted-foreground">Dates may reflect republishing. A date window excludes postings whose publication date is unknown.</p>
        <label className="flex items-center gap-3 text-sm"><input type="checkbox" className="h-4 w-4 accent-primary" checked={remote} disabled={locked} onChange={(event) => setRemote(event.target.checked)} />Remote roles only</label>
        <EmployerSearchPreferences value={preferences} onChange={setPreferences} disabled={locked} error={choice.error} />
        <p className="rounded-lg bg-surface p-4 text-sm leading-6">One reviewed batch finds up to 5 new verified jobs. A completed batch containing 1–5 jobs costs 1 credit; an empty batch costs 0 and restores the reserved credit. Search does not apply to jobs.</p>
        <Button type="submit" className="min-h-11 w-full" disabled={!canReview}>{quote.isPending ? "Checking search quote…" : "Review one-credit search"}</Button>
        {!enabled ? <p className="text-xs leading-5 text-muted-foreground">New credit-based discovery is currently unavailable. Saved search results remain accessible.</p> : null}
      </div>
    </form>
    <div className="min-w-0 space-y-5">
      {review && !pending ? <ServiceChargeReview quote={review.quote} context={{ operation: "job_discovery_batch", inputFingerprint: identity === review.identity ? review.quote.input_fingerprint : "changed",
        resume: selectedResume ? { id: selectedResume.id, filename: selectedResume.filename } : undefined,
        details: [{ label: "Search", value: `${review.input.role} · ${review.input.location || "Any location"}` }, { label: "Batch", value: "Up to 5 new verified employer jobs. Partial results still cost 1 credit. No automatic next batch." },
          { label: "Preferences", value: `${review.input.remote_only ? "Remote only" : "Remote and on-site"}; ${review.input.published_within_days ? `within ${review.input.published_within_days} days` : "any publication date"}. Other selected preferences remain attached to this exact request.` }] }}
        disabled={!enabled || identity !== review.identity} busy={create.isPending} onRefresh={requestQuote} onCancel={() => setReview(null)}
        onAccept={() => { if (!enabled || locked || identity !== review.identity) return; const accepted = { ...review.input, accepted_service_quote_id: review.quote.quote_id, idempotency_key: crypto.randomUUID() }; setPending(accepted); create.mutate(accepted); }} /> : null}
      {quote.isError ? <p role="alert" className="text-sm text-coral">{quote.error.message}</p> : null}
      {create.isError ? <div role="alert" className="surface space-y-3 p-5 text-sm"><p>{create.error.message}</p>{pending ? <><p>The result is uncertain. Check the exact approved request before starting another batch; its original quote and request identity are retained.</p><Button variant="secondary" disabled={create.isPending} onClick={() => create.mutate(pending)}>Check approved search</Button></> : <p>No search was created. Review a fresh quote when ready.</p>}</div> : null}
      {create.isPending ? <div role="status" aria-label="Starting approved search"><LoadingBlock rows={2} /></div> : null}
      {batchId && (progress.isError || progress.pollingPaused) ? <div role="alert" className="surface p-5 text-sm"><p>Automatic search checks are paused. Reading status will not purchase another batch.</p><Button className="mt-3" variant="secondary" disabled={progress.isFetching} onClick={() => void progress.refetch()}>Check search status</Button></div> : null}
      {batch ? <section aria-live="polite" className="min-w-0 space-y-4" aria-label="Discovery batch results">
        <div className="surface p-5 sm:p-6"><h2 className="font-display text-2xl">{batch.status === "preparing" ? "Finding employer jobs" : batch.status === "completed" ? `${batch.delivered_count} new verified ${batch.delivered_count === 1 ? "job" : "jobs"}` : `Search ${batch.status}`}</h2>
          <p className="mt-3 text-sm font-semibold">{batch.charged_credits} {batch.charged_credits === 1 ? "credit" : "credits"} charged · {batch.reserved_credits} held · {batch.refunded_credits} restored</p>
          {batch.partial_batch ? <p className="mt-2 text-sm">Partial batch: {batch.delivered_count} of {batch.desired_count} requested jobs. The reviewed one-credit batch charge applies.</p> : null}
          {batch.status === "completed" && batch.delivered_count === 0 ? <p className="mt-2 text-sm">No new qualifying jobs were found. No credit was charged; the reserved credit was restored.</p> : null}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">Coverage is limited to available indexed employer sources, with up to 1,000 candidates compared. Worldwide coverage is not verified. No further batch starts automatically.</p>
          {batch.status === "preparing" ? <Button className="mt-4" variant="secondary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? "Cancelling…" : "Cancel search"}</Button> : null}
          {cancel.isError ? <p role="alert" className="mt-3 text-sm text-coral">{cancel.error.message} Check the saved search status before taking another action.</p> : null}
        </div>
        {batch.items.map((item, index) => <SavedResultCard key={index} item={item as unknown as SearchResult} preparing={preparingApplication} canPrepare={Boolean(resumeId)} onPrepare={(postingId) => onPrepare(postingId, Number(resumeId))} />)}
      </section> : !review && !pending ? <p className="text-sm leading-6 text-muted-foreground">Choose your resume and preferences, then review the exact service quote. Opening a saved search below is free.</p> : null}
    </div>
  </section>;
}
