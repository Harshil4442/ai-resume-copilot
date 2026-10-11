"use client";

import { EmployerSearchPreferences } from "../../components/EmployerSearchPreferences";
import { EmployerSavedResults, EmployerSearchHistory } from "../../components/EmployerSavedSearches";
import { emptyPreferenceDraft, preferenceDraft, preferenceInput } from "../../lib/searchPreferences";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, LoaderCircle, Search } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { apiGet, apiPostJson } from "../../lib/api";
import { applicationStatusLabels, batchEligibility, employerJobsBase, isSearchPreparation, readSavedSearchQuery, searchSummary, type EmployerApplication, type EmployerSavedResultPage, type EmployerSearchCreateResult, type EmployerSearchPreparation, type EmployerSearchSummary, type JobServiceCatalog } from "../../lib/employerJobs";
import type { ResumeListResponse } from "../../lib/types";

const ApplicationPanel = dynamic(() => import("../../components/EmployerApplicationPanel"), { loading: () => <LoadingBlock rows={2} /> });
const BatchPanel = dynamic(() => import("../../components/EmployerBatchPanel"), { loading: () => <LoadingBlock rows={2} /> });
const dateLabel = (value: string) => new Date(value).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

export default function EmployerJobsPage() {
  const client = useQueryClient();
  const [role, setRole] = useState("");
  const [location, setLocation] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [count, setCount] = useState("10");
  const [remote, setRemote] = useState(false);
  const [days, setDays] = useState("");
  const [preferences, setPreferences] = useState({ ...emptyPreferenceDraft, employment: [...emptyPreferenceDraft.employment] });
  const [excludedEmployers, setExcludedEmployers] = useState<string[]>([]);
  const preferenceChoice = preferenceInput(preferences);
  const [recoverableQuery, setRecoverableQuery] = useState<string | null>(null);
  const [activeSearch, setActiveSearch] = useState<EmployerSearchSummary | null>(null);
  const [preparation, setPreparation] = useState<EmployerSearchPreparation | null>(null);
  const preparationSelection = useRef<string | null>(null);
  const [preparationError, setPreparationError] = useState<string | null>(null);
  const [preparationRetry, setPreparationRetry] = useState(0);
  const [applicationId, setApplicationId] = useState<string | null>(null);
  const [selectedApplications, setSelectedApplications] = useState<string[]>([]);
  const [batchReview, setBatchReview] = useState<{ id?: string; selected?: EmployerApplication[] } | null>(null);
  const applicationIntent = useRef<{ fingerprint: string; key: string } | null>(null);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const catalog = useQuery({ queryKey: ["employer-jobs", "catalog"], queryFn: ({ signal }) => apiGet<JobServiceCatalog>(`${employerJobsBase}/catalog`, signal) });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: ({ signal }) => apiGet<ResumeListResponse>("/resume/list", signal) });
  const applications = useQuery({ queryKey: ["employer-jobs", "applications"], queryFn: ({ signal }) => apiGet<{ items: EmployerApplication[] }>(`${employerJobsBase}/applications`, signal) });
  const requestedCount = Number(count);
  const validCount = Number.isInteger(requestedCount) && requestedCount >= 1 && requestedCount <= (catalog.data?.max_search_jobs ?? 0);
  const estimated = validCount && catalog.data ? requestedCount * catalog.data.search_credits_per_job : null;
  const enoughCredits = estimated !== null && catalog.data !== undefined && catalog.data.balance >= estimated;
  const query = { resume_id: Number(resumeId), role: role.trim(), location: location.trim(), desired_count: requestedCount, remote_only: remote, published_within_days: days ? Number(days) : null, excluded_employers: excludedEmployers, preferences: preferenceChoice.preferences };
  const fingerprint = JSON.stringify(query);
  const recovering = recoverableQuery === fingerprint;

  const refreshBalances = useCallback(() => {
    window.dispatchEvent(new Event("refresh_analysis_units"));
    void client.invalidateQueries({ queryKey: ["employer-jobs", "catalog"] });
    void client.invalidateQueries({ queryKey: ["employer-jobs", "applications"] });
  }, [client]);
  const search = useMutation({
    mutationFn: async () => {
      if (!catalog.data?.enabled || !validCount || preferenceChoice.error !== null || !resumeId || !role.trim() || (!enoughCredits && !recovering)) throw new Error("Check your resume, role, job count and service credit balance before searching.");
      if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
      return apiPostJson<EmployerSearchCreateResult>(`${employerJobsBase}/searches`, { ...query, idempotency_key: intent.current.key });
    },
    onSuccess: (result) => {
      setPreparationError(null);
      if (isSearchPreparation(result)) { preparationSelection.current = result.id; setPreparation(result); setRecoverableQuery(null); void client.invalidateQueries({ queryKey: ["employer-jobs", "search-summaries"] }); refreshBalances(); return; }
      preparationSelection.current = null;
      setPreparation(null); setActiveSearch(searchSummary(result)); intent.current = null; setRecoverableQuery(null);
      void client.invalidateQueries({ queryKey: ["employer-jobs", "search-summaries"] }); refreshBalances();
    },
    onError: () => { setRecoverableQuery(intent.current?.fingerprint ?? null); refreshBalances(); },
  });
  const reviewPreparation = useMutation({
    mutationFn: (identity: string) => apiGet<EmployerSearchPreparation>(`${employerJobsBase}/search-preparations/${encodeURIComponent(identity)}`),
    onSuccess: (current, identity) => { if (preparationSelection.current !== identity) return; setPreparation(current); setPreparationError(null); refreshBalances(); },
  });
  const preparationId = preparation?.id;
  const preparationTerminal = preparation?.status === "failed" || preparation?.status === "cancelled";
  useEffect(() => {
    if (!preparationId || preparationTerminal) return;
    const identity = preparationId;
    const controller = new AbortController();
    let cancelled = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const current = await apiGet<EmployerSearchPreparation>(`${employerJobsBase}/search-preparations/${encodeURIComponent(identity)}`, controller.signal);
        if (cancelled) return;
        setPreparation((previous) => previous?.id === current.id &&
          (previous.status === "failed" || previous.status === "cancelled") &&
          current.status !== "completed" ? previous : current);
        setPreparationError(null);
        if (current.status === "completed") {
          if (!current.completed_search_id) throw new Error("The completed search is not available yet. Retry its status.");
          const firstPage = await apiGet<EmployerSavedResultPage>(`${employerJobsBase}/searches/${encodeURIComponent(current.completed_search_id)}/results?limit=10`, controller.signal);
          if (cancelled) return;
          client.setQueryData(["employer-jobs", "saved-results", current.completed_search_id, null], firstPage);
          setActiveSearch(firstPage.search); setPreparation(null); intent.current = null; setRecoverableQuery(null);
          void client.invalidateQueries({ queryKey: ["employer-jobs", "search-summaries"] }); refreshBalances();
        } else if (current.status === "failed" || current.status === "cancelled") {
          intent.current = null; setRecoverableQuery(null); refreshBalances();
        } else timer = window.setTimeout(() => void poll(), 2000);
      } catch (error) {
        if (!cancelled) setPreparationError(error instanceof Error ? error.message : "Could not read search progress.");
      }
    }
    timer = window.setTimeout(() => void poll(), 2000);
    return () => { cancelled = true; controller.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [preparationId, preparationTerminal, preparationRetry, client, refreshBalances]);
  const searchPreparing = preparation !== null && preparation.status !== "failed" && preparation.status !== "cancelled";
  const searchBusy = search.isPending || searchPreparing;
  const cancelSearch = useMutation({
    mutationFn: () => {
      if (!preparationId) throw new Error("No search is being prepared.");
      return apiPostJson<EmployerSearchPreparation>(`${employerJobsBase}/search-preparations/${encodeURIComponent(preparationId)}/cancel`, {});
    },
    onSuccess: (current) => {
      setPreparation(current); setPreparationError(null);
      setPreparationRetry((value) => value + 1);
      if (current.status === "failed" || current.status === "cancelled") {
        intent.current = null; setRecoverableQuery(null); refreshBalances();
      }
    },
  });
  const prepare = useMutation({
    mutationFn: (postingId: string) => {
      if (!resumeId) throw new Error("Choose a resume for this application.");
      const payload = { posting_id: postingId, resume_id: Number(resumeId), resume_choice: "original" };
      const fingerprint = JSON.stringify(payload);
      if (applicationIntent.current?.fingerprint !== fingerprint) applicationIntent.current = { fingerprint, key: crypto.randomUUID() };
      return apiPostJson<EmployerApplication>(`${employerJobsBase}/applications`, { ...payload, idempotency_key: applicationIntent.current.key });
    },
    onSuccess: (application) => { applicationIntent.current = null; client.setQueryData(["employer-jobs", "application", application.id], application); setApplicationId(application.id); void client.invalidateQueries({ queryKey: ["employer-jobs", "applications"] }); },
  });
  const selectedPackages = (applications.data?.items ?? []).filter((app) => selectedApplications.includes(app.id));
  const selectionValid = selectedPackages.length > 0 && selectedPackages.length === selectedApplications.length && selectedPackages.every((app) => batchEligibility(app, catalog.data) === null);

  return <main className="app-page"><div className="page-container">
    <header className="grid gap-5 border-b border-border pb-7 sm:grid-cols-[1fr_auto] sm:items-start">
      <div><p className="eyebrow">Employer job search</p><h1 className="font-display mt-2 text-4xl font-normal sm:text-5xl">Find your next role. Apply with control.</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Search verified employer career pages, compare your skills, then review the exact resume and answers for each application.</p></div>
      <div className="surface p-4"><p className="data-label">Service credits</p><p className="mt-1 text-2xl font-semibold text-primary">{catalog.data?.balance ?? "—"}</p><Link href="/billing" className="mt-2 inline-block text-sm font-semibold text-primary underline underline-offset-4">Buy credits</Link></div>
    </header>
    <div className="mt-8 grid min-w-0 gap-8 xl:grid-cols-[340px_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-5">
        <form className="surface p-5 sm:p-6" onSubmit={(event) => { event.preventDefault(); search.mutate(); }} aria-labelledby="search-heading">
          <h2 id="search-heading" className="font-display text-2xl">Your search</h2>
          <div className="mt-5 grid gap-4">
            <label className="grid gap-2 text-sm font-semibold">Resume<select className="field-control min-w-0" value={resumeId} onChange={(event) => setResumeId(event.target.value)} required disabled={searchBusy || resumes.isLoading}><option value="">Choose a resume</option>{resumes.data?.resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename} · #{resume.id}{resume.source_available ? "" : " · original file missing"}</option>)}</select></label>
            {resumes.isError ? <p role="alert" className="text-sm text-coral">Could not load resumes. <button type="button" className="underline" onClick={() => void resumes.refetch()}>Retry</button></p> : null}
            <Link href="/resume" className="text-xs font-semibold text-primary underline underline-offset-4">Upload another resume</Link>
            <label className="grid gap-2 text-sm font-semibold">Target role<input className="field-control" value={role} onChange={(event) => setRole(event.target.value)} required maxLength={160} disabled={searchBusy} placeholder="e.g. Backend Engineer" /></label>
            <label className="grid gap-2 text-sm font-semibold">Location<input className="field-control" value={location} onChange={(event) => setLocation(event.target.value)} maxLength={160} disabled={searchBusy} placeholder="City or country" /></label>
            <div className="grid grid-cols-2 gap-3"><label className="grid gap-2 text-sm font-semibold">Jobs to find<input className="field-control" type="number" min={1} max={catalog.data?.max_search_jobs ?? 100} step={1} value={count} onChange={(event) => setCount(event.target.value)} disabled={searchBusy} required /></label><label className="grid gap-2 text-sm font-semibold">Published/released within<select className="field-control" value={days} onChange={(event) => setDays(event.target.value)} disabled={searchBusy}><option value="">Any date</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option></select></label></div><p className="text-xs leading-5 text-muted-foreground">Dates may reflect republishing. A date window excludes postings whose publication date is unknown.</p>
            <label className="flex items-center gap-3 text-sm"><input type="checkbox" className="h-4 w-4 accent-primary" checked={remote} onChange={(event) => setRemote(event.target.checked)} disabled={searchBusy} />Remote roles only</label>
            <EmployerSearchPreferences value={preferences} onChange={setPreferences} disabled={searchBusy} error={preferenceChoice.error} />
            <div className="rounded-lg bg-surface p-4 text-sm leading-6" id="search-cost"><p className="font-semibold">{estimated !== null ? `Up to ${estimated} service credits` : "Checking search price…"}</p><p className="mt-1 text-xs text-muted-foreground">{catalog.data ? `${catalog.data.search_credits_per_job} per new verified job delivered. Unused reservations are returned. Search does not apply to jobs.` : "Prices and availability come from the server."}</p></div>
            <Button type="submit" className="min-h-11 w-full" aria-describedby="search-cost" disabled={!catalog.data?.enabled || !validCount || preferenceChoice.error !== null || !resumeId || !role.trim() || (!enoughCredits && !recovering) || searchBusy}>{searchBusy ? <LoaderCircle size={16} className="animate-spin" /> : <Search size={16} />}{searchBusy ? "Finding employer jobs…" : "Find jobs"}</Button>
            {catalog.isError ? <p role="alert" className="text-sm text-coral">Could not read prices and availability. <button type="button" className="underline" onClick={() => void catalog.refetch()}>Retry</button></p> : catalog.data && !catalog.data.enabled ? <p className="text-xs leading-5 text-muted-foreground">Employer search is not enabled yet. You can continue tracking roles in <Link href="/workspace" className="underline text-primary">Workspace</Link>.</p> : estimated !== null && !enoughCredits && !recovering ? <p className="text-xs leading-5 text-muted-foreground">You need {estimated} service credits for this search. <Link className="underline text-primary" href="/billing">Review credit packs</Link>.</p> : null}
          </div>
        </form>
        <section className="border-y border-border py-5"><h2 className="text-sm font-semibold">Current source coverage</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">{catalog.data?.sources.length ?? 0} configured employer sources. Coverage is limited to these sources; results are not a census of all jobs.</p><ul className="mt-3 grid gap-2">{catalog.data?.sources.map((source) => <li key={source.id} className="text-xs"><span className="font-semibold">{source.employer}</span><span className="text-muted-foreground"> · {source.status === "healthy" ? `Checked ${source.last_success_at ? dateLabel(source.last_success_at) : "date unknown"}` : source.status.replaceAll("_", " ")}</span></li>)}</ul></section>
        <EmployerSearchHistory selectedId={activeSearch?.id} onSelect={(item) => { const savedQuery = readSavedSearchQuery(item.query); setPreparation(null); setPreparationError(null); preparationSelection.current = item.status !== "completed" ? item.id : null; if (item.status !== "completed") { setActiveSearch(null); reviewPreparation.mutate(item.id); } else setActiveSearch(item); setResumeId(savedQuery ? String(savedQuery.resume_id) : ""); setRole(savedQuery?.role ?? ""); setLocation(savedQuery?.location ?? ""); setRemote(savedQuery?.remote_only ?? false); setDays(savedQuery?.published_within_days ? String(savedQuery.published_within_days) : ""); setCount(savedQuery ? String(savedQuery.desired_count ?? item.desired_count) : ""); setPreferences(preferenceDraft(savedQuery?.preferences)); setExcludedEmployers(savedQuery?.excluded_employers ?? []); }} />
      </aside>
      <div className="min-w-0 space-y-8">
        {search.isError ? <div role="alert" className="rounded-lg border border-coral/30 bg-coral/5 p-4 text-sm leading-6 text-coral">{search.error.message} If the request was interrupted, retry the same search to check its existing result before starting a different paid search.</div> : null}
        {reviewPreparation.isError ? <p role="alert" className="text-sm text-coral">Could not open search progress. Select the search again to retry; its reserved credits and results are unchanged.</p> : null}
        {reviewPreparation.isPending ? <div role="status" aria-label="Opening search progress"><LoadingBlock rows={2} /></div> : null}
        {prepare.isError ? <p role="alert" className="text-sm text-coral">{prepare.error.message}</p> : null}
        {search.isPending ? <section aria-live="polite"><p className="mb-4 text-sm text-muted-foreground">Checking employer postings and comparing the skills in your resume.</p><LoadingBlock rows={3} /></section> : preparation ? <section className="surface p-5 sm:p-6" aria-live="polite" aria-labelledby="search-progress-heading">
          <p className="eyebrow">Employer search</p><h2 id="search-progress-heading" className="font-display mt-2 text-2xl">{preparation.status === "completed" ? "Opening your saved results" : searchPreparing ? "Preparing your search" : "Search did not complete"}</h2>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">{searchPreparing && preparation.status !== "completed" ? "Comparing eligible employer postings with your resume and choices. Only newly delivered jobs will be charged." : preparation.status === "completed" ? "Your search completed. Reading its first saved result page." : "You can start another search or return to your saved results."}</p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">Search accepted {new Date(preparation.as_of).toLocaleString("en-IN")} · {preparation.candidates_examined} candidates examined.</p>
          <p className="mt-2 text-sm font-semibold">{preparation.status === "completed" ? `${preparation.charged_credits} service credits charged` : "No service credits charged"}</p>
          {preparation.reserved_credits !== undefined ? <p className="mt-2 text-xs leading-5 text-muted-foreground">{preparation.reserved_credits} service credits held · {preparation.refunded_credits ?? 0} returned. Unused credits return when the search completes or is cancelled.</p> : null}
          <p className="mt-2 text-xs leading-5 text-muted-foreground">Accepted quote: {preparation.accepted_quote.unit_price} per new job · up to {preparation.accepted_quote.maximum_credits} service credits. Checking progress and opening saved results are free.</p>
          {preparationError ? <div role="alert" className="mt-4 text-sm leading-6"><p>{preparationError}</p><Button type="button" variant="secondary" className="mt-3 min-h-11" onClick={() => { setPreparationError(null); setPreparationRetry((value) => value + 1); }}>Retry search status</Button></div> : searchPreparing ? <div className="mt-4" role="status" aria-label="Search preparation in progress"><LoaderCircle size={20} className="animate-spin" /></div> : <Button type="button" variant="secondary" className="mt-4 min-h-11" onClick={() => { setPreparation(null); setPreparationError(null); }}>Return to saved results</Button>}
          {searchPreparing && preparation.status !== "completed" ? <Button type="button" variant="secondary" className="mt-4 min-h-11" disabled={cancelSearch.isPending} onClick={() => cancelSearch.mutate()}>{cancelSearch.isPending ? "Cancelling search…" : "Cancel search"}</Button> : null}
          {cancelSearch.isError ? <p role="alert" className="mt-3 text-sm leading-6">{cancelSearch.error.message}</p> : null}
        </section> : activeSearch ? <EmployerSavedResults key={activeSearch.id} search={activeSearch} preparing={prepare.isPending} canPrepare={Boolean(resumeId)} onPrepare={(postingId) => prepare.mutate(postingId)} /> : <EmptyState icon={Search} title="Search with your resume and preferences" description="Choose how many verified jobs to find. Review the service credit price before starting." />}
        <section aria-labelledby="applications-heading" className="border-t border-border pt-7"><h2 id="applications-heading" className="font-display text-2xl">Your applications</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Each job keeps its own resume choice, review and submission status.</p>
          <div className="mt-5 space-y-3 rounded-xl border border-border bg-surface p-4"><h3 className="font-semibold">Review a fixed application batch</h3><p className="text-xs leading-5 text-muted-foreground">Select complete applications supported by a permissioned API, then review every exact job, file, answer, action and fixed service credit price. The current manual sources use individual employer handoffs.</p>{catalog.data?.admission_limits ? <p className="text-xs leading-5">Maximum {catalog.data.admission_limits.batch_limit} jobs and {catalog.data.admission_limits.batch_credit_limit} service credits per batch. Approval never includes future jobs.</p> : null}<Button variant="secondary" className="min-h-11 w-full sm:w-auto" disabled={!selectionValid || applications.isFetching} onClick={() => setBatchReview({ selected: selectedPackages })}>Review selected batch ({selectedPackages.length})</Button>{!catalog.data?.auto_submit_enabled ? <p className="text-xs leading-5 text-muted-foreground">API batch submission is currently disabled.</p> : null}</div>
          {applications.isLoading ? <div className="mt-4"><LoadingBlock rows={2} /></div> : applications.isError ? <div className="mt-4 flex items-center gap-3 text-sm" role="alert"><CircleAlert size={16} /><p>Could not load applications.</p><Button variant="secondary" size="sm" onClick={() => void applications.refetch()}>Retry</Button></div> : <div className="mt-4 divide-y divide-border">{applications.data?.items.map((application) => {
            const ineligible = batchEligibility(application, catalog.data);
            return <div key={application.id} className="min-w-0 py-4">
              <button type="button" onClick={() => setApplicationId(application.id)} className="flex min-h-11 w-full flex-wrap items-start justify-between gap-3 py-2 text-left hover:bg-surface"><span className="min-w-0"><span className="block break-words text-sm font-semibold">{application.posting.title}</span><span className="mt-1 block text-xs text-muted-foreground">{application.posting.employer}</span></span><span className={`inline-flex shrink-0 items-center gap-1.5 text-xs ${application.status === "confirmed" ? "text-primary" : "text-muted-foreground"}`}>{application.status === "confirmed" ? <CheckCircle2 size={14} /> : null}{applicationStatusLabels[application.status]}</span></button>
              {application.batch_id ? <Button variant="ghost" className="mt-2 min-h-11" onClick={() => setBatchReview({ id: application.batch_id! })}>View this application's batch</Button> : application.application_mode === "api" ? <label className="mt-2 flex min-h-11 items-start gap-3 text-xs leading-5"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={selectedApplications.includes(application.id)} disabled={Boolean(ineligible)} onChange={(event) => setSelectedApplications((previous) => event.target.checked ? [...previous, application.id] : previous.filter((value) => value !== application.id))} /><span>Select {application.posting.title} for exact batch review{ineligible ? <span className="mt-1 block text-muted-foreground">{ineligible}</span> : <span className="mt-1 block text-muted-foreground">{application.credit_cost} service credits · {application.artifact?.filename}</span>}</span></label> : <p className="mt-2 text-xs leading-5 text-muted-foreground">Individual manual handoff. This employer is not included in API batches.</p>}
            </div>;
          })}{!applications.data?.items.length ? <p className="py-5 text-sm text-muted-foreground">Prepare a result above to begin. Nothing is submitted when you search.</p> : null}</div>}
        </section>
      </div>
    </div>
    {applicationId ? <ApplicationPanel key={applicationId} id={applicationId} onClose={() => setApplicationId(null)} onChange={refreshBalances} resumes={resumes.data?.resumes ?? []} /> : null}
    {batchReview ? <BatchPanel key={batchReview.id ?? selectedApplications.join(":")} {...batchReview} onClose={() => { setBatchReview(null); setSelectedApplications([]); refreshBalances(); }} onChange={refreshBalances} /> : null}
  </div></main>;
}
