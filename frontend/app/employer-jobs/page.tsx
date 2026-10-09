"use client";

import { EmployerSearchPreferences } from "../../components/EmployerSearchPreferences";
import { emptyPreferenceDraft, preferenceDraft, preferenceInput } from "../../lib/searchPreferences";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, BriefcaseBusiness, CheckCircle2, CircleAlert, Clock3, LoaderCircle, MapPin, RefreshCw, Search, ShieldCheck } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { apiGet, apiPostJson } from "../../lib/api";
import { applicationStatusLabels, batchEligibility, employerJobsBase, safeEmployerUrl, type EmployerApplication, type EmployerSearch, type JobServiceCatalog } from "../../lib/employerJobs";
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
  const [activeSearch, setActiveSearch] = useState<EmployerSearch | null>(null);
  const [applicationId, setApplicationId] = useState<string | null>(null);
  const [selectedApplications, setSelectedApplications] = useState<string[]>([]);
  const [batchReview, setBatchReview] = useState<{ id?: string; selected?: EmployerApplication[] } | null>(null);
  const applicationIntent = useRef<{ fingerprint: string; key: string } | null>(null);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const catalog = useQuery({ queryKey: ["employer-jobs", "catalog"], queryFn: ({ signal }) => apiGet<JobServiceCatalog>(`${employerJobsBase}/catalog`, signal) });
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: ({ signal }) => apiGet<ResumeListResponse>("/resume/list", signal) });
  const searches = useQuery({ queryKey: ["employer-jobs", "searches"], queryFn: ({ signal }) => apiGet<{ items: EmployerSearch[] }>(`${employerJobsBase}/searches`, signal) });
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
      return apiPostJson<EmployerSearch>(`${employerJobsBase}/searches`, { ...query, idempotency_key: intent.current.key });
    },
    onSuccess: (result) => { setActiveSearch(result); intent.current = null; setRecoverableQuery(null); void client.invalidateQueries({ queryKey: ["employer-jobs", "searches"] }); refreshBalances(); },
    onError: () => { setRecoverableQuery(intent.current?.fingerprint ?? null); refreshBalances(); },
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
  const results = activeSearch?.items ?? [];
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
            <label className="grid gap-2 text-sm font-semibold">Resume<select className="field-control min-w-0" value={resumeId} onChange={(event) => setResumeId(event.target.value)} required disabled={search.isPending || resumes.isLoading}><option value="">Choose a resume</option>{resumes.data?.resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename} · #{resume.id}{resume.source_available ? "" : " · original file missing"}</option>)}</select></label>
            {resumes.isError ? <p role="alert" className="text-sm text-coral">Could not load resumes. <button type="button" className="underline" onClick={() => void resumes.refetch()}>Retry</button></p> : null}
            <Link href="/resume" className="text-xs font-semibold text-primary underline underline-offset-4">Upload another resume</Link>
            <label className="grid gap-2 text-sm font-semibold">Target role<input className="field-control" value={role} onChange={(event) => setRole(event.target.value)} required maxLength={160} disabled={search.isPending} placeholder="e.g. Backend Engineer" /></label>
            <label className="grid gap-2 text-sm font-semibold">Location<input className="field-control" value={location} onChange={(event) => setLocation(event.target.value)} maxLength={160} disabled={search.isPending} placeholder="City or country" /></label>
            <div className="grid grid-cols-2 gap-3"><label className="grid gap-2 text-sm font-semibold">Jobs to find<input className="field-control" type="number" min={1} max={catalog.data?.max_search_jobs ?? 100} step={1} value={count} onChange={(event) => setCount(event.target.value)} disabled={search.isPending} required /></label><label className="grid gap-2 text-sm font-semibold">Published/released within<select className="field-control" value={days} onChange={(event) => setDays(event.target.value)} disabled={search.isPending}><option value="">Any date</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option></select></label></div><p className="text-xs leading-5 text-muted-foreground">Dates may reflect republishing. A date window excludes postings whose publication date is unknown.</p>
            <label className="flex items-center gap-3 text-sm"><input type="checkbox" className="h-4 w-4 accent-primary" checked={remote} onChange={(event) => setRemote(event.target.checked)} disabled={search.isPending} />Remote roles only</label>
            <EmployerSearchPreferences value={preferences} onChange={setPreferences} disabled={search.isPending} error={preferenceChoice.error} />
            <div className="rounded-lg bg-surface p-4 text-sm leading-6" id="search-cost"><p className="font-semibold">{estimated !== null ? `Up to ${estimated} service credits` : "Checking search price…"}</p><p className="mt-1 text-xs text-muted-foreground">{catalog.data ? `${catalog.data.search_credits_per_job} per new verified job delivered. Unused reservations are returned. Search does not apply to jobs.` : "Prices and availability come from the server."}</p></div>
            <Button type="submit" className="min-h-11 w-full" aria-describedby="search-cost" disabled={!catalog.data?.enabled || !validCount || preferenceChoice.error !== null || !resumeId || !role.trim() || (!enoughCredits && !recovering) || search.isPending}>{search.isPending ? <LoaderCircle size={16} className="animate-spin" /> : <Search size={16} />}{search.isPending ? "Finding employer jobs…" : "Find jobs"}</Button>
            {catalog.isError ? <p role="alert" className="text-sm text-coral">Could not read prices and availability. <button type="button" className="underline" onClick={() => void catalog.refetch()}>Retry</button></p> : catalog.data && !catalog.data.enabled ? <p className="text-xs leading-5 text-muted-foreground">Employer search is not enabled yet. You can continue tracking roles in <Link href="/workspace" className="underline text-primary">Workspace</Link>.</p> : estimated !== null && !enoughCredits && !recovering ? <p className="text-xs leading-5 text-muted-foreground">You need {estimated} service credits for this search. <Link className="underline text-primary" href="/billing">Review credit packs</Link>.</p> : null}
          </div>
        </form>
        <section className="border-y border-border py-5"><h2 className="text-sm font-semibold">Current source coverage</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">{catalog.data?.sources.length ?? 0} configured employer sources. Coverage is limited to these sources; results are not a census of all jobs.</p><ul className="mt-3 grid gap-2">{catalog.data?.sources.map((source) => <li key={source.id} className="text-xs"><span className="font-semibold">{source.employer}</span><span className="text-muted-foreground"> · {source.status === "healthy" ? `Checked ${source.last_success_at ? dateLabel(source.last_success_at) : "date unknown"}` : source.status.replaceAll("_", " ")}</span></li>)}</ul></section>
        {searches.data?.items.length ? <section><h2 className="text-sm font-semibold">Saved searches</h2><div className="mt-3 grid gap-2">{searches.data.items.slice(0, 10).map((item) => <button key={item.id} type="button" onClick={() => { setActiveSearch(item); setResumeId(String(item.query.resume_id)); setRole(item.query.role); setLocation(item.query.location); setRemote(item.query.remote_only); setDays(item.query.published_within_days ? String(item.query.published_within_days) : ""); setCount(String(item.query.desired_count ?? item.desired_count)); setPreferences(preferenceDraft(item.query.preferences)); setExcludedEmployers(item.query.excluded_employers ?? []); }} className={`rounded-lg border p-3 text-left text-sm transition-colors hover:border-accent ${activeSearch?.id === item.id ? "border-primary" : "border-border"}`}><span className="block font-semibold">{item.query.role}</span><span className="mt-1 block text-xs text-muted-foreground">{item.delivered_count} jobs · {dateLabel(item.created_at)}</span></button>)}</div></section> : null}
      </aside>
      <div className="min-w-0 space-y-8">
        {search.isError ? <div role="alert" className="rounded-lg border border-coral/30 bg-coral/5 p-4 text-sm leading-6 text-coral">{search.error.message} If the request was interrupted, retry the same search to check its existing result before starting a different paid search.</div> : null}
        {prepare.isError ? <p role="alert" className="text-sm text-coral">{prepare.error.message}</p> : null}
        {search.isPending ? <section aria-live="polite"><p className="mb-4 text-sm text-muted-foreground">Checking employer postings and comparing the skills in your resume.</p><LoadingBlock rows={3} /></section> : activeSearch ? <section aria-labelledby="results-heading"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">Search results</p><h2 id="results-heading" className="font-display mt-2 text-3xl">{activeSearch.delivered_count} of {activeSearch.desired_count} requested jobs</h2><p className="mt-2 text-xs text-muted-foreground">{activeSearch.charged_credits} service credits charged · {activeSearch.refunded_credits} returned</p></div><Button variant="secondary" size="sm" onClick={() => void searches.refetch()}><RefreshCw size={14} />Refresh history</Button></div><p className="mt-3 text-xs leading-5 text-muted-foreground">{activeSearch.scope?.known_preference_conflicts ?? 0} known preference conflicts excluded from the bounded candidate set. Missing facts remain visible; results do not certify eligibility.{activeSearch.scope?.candidate_limit_reached ? " Only the first 1,000 current candidates were compared. Narrow the role or location to review more." : ""}</p><div className="mt-5 grid gap-4">{results.map(({ posting, fit, preference_evaluation }) => <article key={posting.id} className="surface min-w-0 p-5 sm:p-6"><div className="flex items-start gap-4"><span className="icon-tile h-10 w-10"><BriefcaseBusiness size={18} /></span><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-primary">{posting.employer}</p><h3 className="mt-1 break-words text-lg font-semibold">{posting.title}</h3><p className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground"><span className="inline-flex items-center gap-1"><MapPin size={12} />{posting.location || "Location not stated"}</span><span className="inline-flex items-center gap-1"><Clock3 size={12} />{posting.publication_at ? `${posting.publication_kind === "release_or_republication" ? "Released or republished" : "Published"} ${dateLabel(posting.publication_at)}` : "Publication date unknown"}</span></p></div></div><div className="mt-5 rounded-lg bg-surface p-4"><p className="text-sm font-semibold">Basic skill fit · {Math.round(fit.score)}%</p><p className="mt-1 text-xs leading-5 text-muted-foreground">Skill overlap, not a hiring probability. {fit.reasons.join(" ")}</p>{fit.matched_skills.length ? <p className="mt-2 text-xs text-primary">Matches: {fit.matched_skills.join(", ")}</p> : null}{fit.missing_evidence.length ? <p className="mt-2 text-xs text-muted-foreground">Missing resume evidence: {fit.missing_evidence.join(", ")}</p> : null}</div>{preference_evaluation && Object.values(preference_evaluation.states).includes("match") ? <p className="mt-3 text-xs leading-5 text-primary">Employer facts match these choices: {Object.entries(preference_evaluation.states).filter(([, state]) => state === "match").map(([name]) => name.replaceAll("_", " ")).join(", ")}. Review remaining requirements with the employer.</p> : null}{preference_evaluation?.messages.length ? <div className="mt-4 rounded-lg border border-border p-3"><p className="text-xs font-semibold">Preferences to review</p><ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">{preference_evaluation.messages.map((message) => <li key={message}>{message}</li>)}</ul></div> : null}<div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><p className="inline-flex items-center gap-2 text-xs text-muted-foreground"><ShieldCheck size={14} />Employer origin checked · {posting.application_mode === "manual" || posting.application_mode === "manual_handoff" ? "Employer portal handoff" : "Automatic application review available"}</p><div className="flex flex-wrap gap-2">{safeEmployerUrl(posting.canonical_url) ? <Button asChild variant="secondary" size="sm"><a href={safeEmployerUrl(posting.canonical_url)!} target="_blank" rel="noopener noreferrer">View employer job<ArrowUpRight size={14} /></a></Button> : null}<Button size="sm" disabled={prepare.isPending || !resumeId} onClick={() => prepare.mutate(posting.id)}>Prepare application</Button></div></div></article>)}</div>{!results.length ? <EmptyState icon={Search} title="No verified jobs found in this scope" description="Try another role, location or date window. Returned reservations are shown above." /> : null}</section> : <EmptyState icon={Search} title="Search with your resume and preferences" description="Choose how many verified jobs to find. Review the service credit price before starting." />}
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
