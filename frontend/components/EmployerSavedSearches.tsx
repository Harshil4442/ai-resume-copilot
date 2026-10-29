"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, BriefcaseBusiness, Clock3, MapPin, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { apiGet } from "../lib/api";
import { employerJobsBase, readSavedSearchQuery, readSavedSearchQuote, safeEmployerUrl, savedPreferenceConflicts, savedSearchRole, type EmployerSavedResultPage, type EmployerSearchSummary, type EmployerSearchSummaryPage, type SearchResult } from "../lib/employerJobs";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";
import { LoadingBlock } from "./ui/LoadingBlock";

const PAGE_SIZE = 10;
const dateLabel = (value: string) => new Date(value).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

function pageQuery(cursor: string | null): string {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

// The shared session owner boundary supplies a fresh QueryClient and remounts
// page-local state on owner changes. No cross-owner placeholder data is used.
export function EmployerSearchHistory({ selectedId, onSelect, disabled = false }: {
  selectedId: string | undefined;
  onSelect: (summary: EmployerSearchSummary) => void;
  disabled?: boolean;
}) {
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const history = useQuery({
    queryKey: ["employer-jobs", "search-summaries", cursor],
    queryFn: ({ signal }) => apiGet<EmployerSearchSummaryPage>(`${employerJobsBase}/search-summaries?${pageQuery(cursor)}`, signal),
  });
  return <section aria-labelledby="saved-searches-heading">
    <h2 id="saved-searches-heading" className="text-sm font-semibold">Saved searches</h2>
    <p className="mt-2 text-xs leading-5 text-muted-foreground">Opening saved results does not spend credits.</p>
    {history.isLoading ? <div className="mt-3" role="status" aria-label="Loading saved searches"><LoadingBlock rows={2} /></div>
      : history.isError ? <div className="mt-3 text-sm" role="alert"><p>Could not load saved searches.</p><Button variant="secondary" size="sm" className="mt-2 min-h-11" onClick={() => void history.refetch()}>Retry saved searches</Button></div>
        : <><div className="mt-3 grid gap-2">{history.data?.items.map((item) => <button key={item.id} type="button" disabled={disabled} aria-pressed={selectedId === item.id} onClick={() => onSelect(item)} className={`min-h-11 rounded-lg border p-3 text-left text-sm transition-colors hover:border-accent ${selectedId === item.id ? "border-primary" : "border-border"}`}><span className="block break-words font-semibold">{savedSearchRole(item.query)}</span><span className="mt-1 block text-xs text-muted-foreground">{item.status === "preparing" ? "Preparing search" : item.status === "failed" ? "Search failed" : item.status === "cancelled" ? "Search cancelled" : `${item.delivered_count} jobs`} · {dateLabel(item.created_at)}</span>{!readSavedSearchQuery(item.query) ? <span className="mt-1 block text-xs text-muted-foreground">Saved search choices need review.</span> : null}</button>)}</div>
          {!history.data?.items.length ? <p className="mt-3 text-xs text-muted-foreground">No saved searches on this page.</p> : null}</>}
    <nav aria-label="Saved search history pages" className="mt-3 flex flex-wrap gap-2">
      <Button variant="secondary" size="sm" className="min-h-11" disabled={cursors.length <= 1 || history.isFetching} onClick={() => setCursors((values) => values.slice(0, -1))}>Previous searches</Button>
      <Button variant="secondary" size="sm" className="min-h-11" disabled={!history.data?.next_cursor || history.isFetching || history.isError} onClick={() => { const next = history.data?.next_cursor; if (next) setCursors((values) => [...values, next]); }}>Next searches</Button>
    </nav>
  </section>;
}

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

function readableResult(item: SearchResult): boolean {
  const { posting, fit } = item;
  return Boolean(posting && fit && typeof posting.id === "string" && posting.id.length > 0 && typeof posting.title === "string" &&
    typeof posting.employer === "string" && (posting.location == null || typeof posting.location === "string") &&
    (posting.publication_at == null || typeof posting.publication_at === "string") &&
    Number.isFinite(fit.score) && stringList(fit.reasons) && stringList(fit.matched_skills) && stringList(fit.missing_evidence));
}

export function SavedResultCard({ item, preparing, canPrepare, onPrepare }: {
  item: SearchResult;
  preparing: boolean;
  canPrepare: boolean;
  onPrepare: (postingId: string) => void;
}) {
  if (!readableResult(item)) return <article className="surface min-w-0 p-5 sm:p-6"><h3 className="text-lg font-semibold">Saved job details need review</h3><p className="mt-2 text-sm text-muted-foreground">This saved entry could not be displayed safely. Its position and original search charges are preserved; no new search or application was started.</p></article>;
  const { posting, fit } = item;
  const rawPreferences = item.preference_evaluation;
  const preference_evaluation = rawPreferences && rawPreferences.states &&
    typeof rawPreferences.states === "object" && !Array.isArray(rawPreferences.states) &&
    Object.values(rawPreferences.states).every((state) => state === "match" || state === "unknown" || state === "conflict") &&
    stringList(rawPreferences.messages) ? rawPreferences : undefined;
  const employerUrl = safeEmployerUrl(posting.canonical_url);
  return <article className="surface min-w-0 p-5 sm:p-6">
    <div className="flex items-start gap-4"><span className="icon-tile h-10 w-10"><BriefcaseBusiness size={18} /></span><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-primary">{posting.employer}</p><h3 className="mt-1 break-words text-lg font-semibold">{posting.title}</h3><p className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground"><span className="inline-flex items-center gap-1"><MapPin size={12} />{posting.location || "Location not stated"}</span><span className="inline-flex items-center gap-1"><Clock3 size={12} />{posting.publication_at ? `${posting.publication_kind === "release_or_republication" ? "Released or republished" : "Published"} ${dateLabel(posting.publication_at)}` : "Publication date unknown"}</span></p></div></div>
    <div className="mt-5 rounded-lg bg-surface p-4"><p className="text-sm font-semibold">Basic skill fit · {Math.round(fit.score)}%</p><p className="mt-1 text-xs leading-5 text-muted-foreground">Skill overlap, not a hiring probability. {fit.reasons.join(" ")}</p>{fit.matched_skills.length ? <p className="mt-2 text-xs text-primary">Matches: {fit.matched_skills.join(", ")}</p> : null}{fit.missing_evidence.length ? <p className="mt-2 text-xs text-muted-foreground">Missing resume evidence: {fit.missing_evidence.join(", ")}</p> : null}</div>
    {preference_evaluation && Object.values(preference_evaluation.states).includes("match") ? <p className="mt-3 text-xs leading-5 text-primary">Employer facts match these choices: {Object.entries(preference_evaluation.states).filter(([, state]) => state === "match").map(([name]) => name.replaceAll("_", " ")).join(", ")}. Review remaining requirements with the employer.</p> : null}
    {preference_evaluation?.messages.length ? <div className="mt-4 rounded-lg border border-border p-3"><p className="text-xs font-semibold">Preferences to review</p><ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">{preference_evaluation.messages.map((message, index) => <li key={`${index}:${message}`}>{message}</li>)}</ul></div> : null}
    {rawPreferences && !preference_evaluation ? <p className="mt-3 text-xs leading-5 text-muted-foreground">Saved job preferences need review. Check the employer requirements before preparing an application.</p> : null}
    <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><p className="inline-flex items-center gap-2 text-xs text-muted-foreground"><ShieldCheck size={14} />Employer origin checked · {posting.application_mode === "api" ? "Automatic application review available" : "Employer portal handoff"}</p><div className="flex flex-wrap gap-2">{employerUrl ? <Button asChild variant="secondary" size="sm" className="min-h-11"><a href={employerUrl} target="_blank" rel="noopener noreferrer">View employer job<ArrowUpRight size={14} /></a></Button> : null}<Button size="sm" className="min-h-11" disabled={preparing || !canPrepare} onClick={() => onPrepare(posting.id)}>Prepare application</Button></div></div>
  </article>;
}

// Mount with key={search.id} so selecting another search resets its page trail.
export function EmployerSavedResults({ search, preparing, canPrepare, onPrepare }: {
  search: EmployerSearchSummary;
  preparing: boolean;
  canPrepare: boolean;
  onPrepare: (postingId: string) => void;
}) {
  const client = useQueryClient();
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const page = useQuery({
    queryKey: ["employer-jobs", "saved-results", search.id, cursor],
    queryFn: ({ signal }) => apiGet<EmployerSavedResultPage>(`${employerJobsBase}/searches/${encodeURIComponent(search.id)}/results?${pageQuery(cursor)}`, signal),
  });
  const saved = page.data?.search ?? search;
  const quote = readSavedSearchQuote(saved.scope?.price_quote);
  const conflicts = savedPreferenceConflicts(saved.scope?.known_preference_conflicts);
  return <section aria-labelledby="results-heading">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">Saved search results</p><h2 id="results-heading" className="font-display mt-2 text-3xl">{saved.delivered_count} of {saved.desired_count} requested jobs</h2><p className="mt-2 text-xs text-muted-foreground">{saved.charged_credits} service credits charged · {saved.refunded_credits} returned</p></div><Button variant="secondary" size="sm" className="min-h-11" onClick={() => void client.invalidateQueries({ queryKey: ["employer-jobs", "search-summaries"] })}><RefreshCw size={14} />Refresh history</Button></div>
    {quote ? <p className="mt-2 text-xs leading-5 text-muted-foreground">Saved quote: {quote.unit_price} per new job · up to {quote.maximum_credits} service credits for {quote.requested_count} requested jobs.</p> : saved.scope?.price_quote !== undefined ? <p className="mt-2 text-xs leading-5 text-muted-foreground">Original price quote needs review. Saved charges and returned credits are shown above.</p> : null}
    {!readSavedSearchQuery(saved.query) ? <p className="mt-2 text-xs leading-5 text-muted-foreground">Saved search choices need review. Your results and charges are preserved. Choose a resume before preparing an application; review your preferences before starting a new search.</p> : null}
    <p className="mt-3 text-xs leading-5 text-muted-foreground">{conflicts !== null ? `${conflicts} known preference conflicts excluded from ${saved.scope?.catalogue_plan_version ? "eligible indexed postings" : "the bounded candidate set"}.` : "Saved preference metadata needs review."} Missing facts remain visible; results do not certify eligibility.{saved.scope?.candidate_limit_reached === true ? " Only the first 1,000 current candidates were compared. Narrow the role or location to review more." : ""}</p>
    {saved.scope?.catalogue_as_of ? <p className="mt-2 text-xs leading-5 text-muted-foreground">Catalogue as of {new Date(saved.scope.catalogue_as_of).toLocaleString("en-IN")}.</p> : null}
    <p className="mt-2 text-xs leading-5 text-muted-foreground">These pages show jobs already saved by this search. They do not find new jobs or spend credits. Coverage remains limited to the indexed employer sources.</p>
    {page.isLoading ? <div className="mt-5" role="status" aria-label="Loading saved results"><LoadingBlock rows={3} /></div>
      : page.isError ? <div className="mt-5 text-sm" role="alert"><p>Could not load this saved result page. Your saved search and charges are unchanged.</p><Button variant="secondary" size="sm" className="mt-2 min-h-11" onClick={() => void page.refetch()}>Retry saved results</Button>{cursors.length > 1 ? <Button variant="secondary" size="sm" className="ml-2 mt-2 min-h-11" onClick={() => { setCursors([null]); void client.invalidateQueries({ queryKey: ["employer-jobs", "saved-results", search.id] }); }}>Return to first results</Button> : null}</div>
        : <><p aria-live="polite" className="mt-4 text-xs text-muted-foreground">{page.data?.total_items ? `Showing ${page.data.start_index + 1}–${page.data.start_index + page.data.items.length} of ${page.data.total_items} saved jobs` : "This search saved no jobs."}</p><div className="mt-5 grid gap-4">{page.data?.items.map((item, index) => <SavedResultCard key={page.data!.start_index + index} item={item} preparing={preparing} canPrepare={canPrepare} onPrepare={onPrepare} />)}</div>{page.data?.total_items === 0 ? <EmptyState icon={Search} title="No jobs saved in this search" description="Try another role, location or date window. This saved result reflects the search's original scope." /> : null}</>}
    <nav aria-label="Saved result pages" className="mt-5 flex flex-wrap gap-2">
      <Button variant="secondary" size="sm" className="min-h-11" disabled={cursors.length <= 1 || page.isFetching} onClick={() => setCursors((values) => values.slice(0, -1))}>Previous results</Button>
      <Button variant="secondary" size="sm" className="min-h-11" disabled={!page.data?.next_cursor || page.isFetching || page.isError} onClick={() => { const next = page.data?.next_cursor; if (next) setCursors((values) => [...values, next]); }}>Next results</Button>
    </nav>
  </section>;
}
