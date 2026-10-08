"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, RefreshCw, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { apiDownload, apiGet, apiPostJson } from "../lib/api";
import { applicationStatusLabels, batchEligibility, batchItemMatches, employerJobsBase, safeEmployerUrl, type EmployerApplication, type EmployerApplicationBatch, type EmployerBatchItem, type JobServiceCatalog } from "../lib/employerJobs";
import { ArtifactPreview } from "./EmployerApplicationPanel";
import { Button } from "./ui/Button";
import { LoadingBlock } from "./ui/LoadingBlock";

type Props = { id?: string; selected?: EmployerApplication[]; onClose: () => void; onChange: () => void };
const actions = ["fill", "upload", "submit"] as const;
const timestamp = (value: string) => new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

export default function EmployerBatchPanel({ id, selected = [], onClose, onChange }: Props) {
  const client = useQueryClient();
  const [batchId, setBatchId] = useState(id);
  const [budget, setBudget] = useState(String(selected.reduce((total, app) => total + app.credit_cost, 0)));
  const [reviews, setReviews] = useState<Record<string, boolean>>({});
  const [now, setNow] = useState(Date.now);
  const intent = useRef<{ fingerprint: string; key: string } | null>(null);
  const polledStatuses = useRef<Record<string, EmployerApplication["status"]>>({});
  const catalog = useQuery({ queryKey: ["employer-jobs", "catalog"], queryFn: ({ signal }) => apiGet<JobServiceCatalog>(`${employerJobsBase}/catalog`, signal), staleTime: 0 });
  const batch = useQuery({
    queryKey: ["employer-jobs", "batch", batchId],
    queryFn: ({ signal }) => apiGet<EmployerApplicationBatch>(`${employerJobsBase}/application-batches/${encodeURIComponent(batchId!)}`, signal),
    enabled: Boolean(batchId), staleTime: 0,
    refetchInterval: (query) => Object.values(query.state.data?.application_statuses ?? {}).some((status) => ["queued", "submitting"].includes(status)) ? 2000 : false,
  });
  const ids = batch.data?.items.map((item) => item.application_id) ?? selected.map((app) => app.id);
  const applications = useQueries({ queries: ids.map((applicationId) => ({
    queryKey: ["employer-jobs", "application", applicationId],
    queryFn: ({ signal }: { signal: AbortSignal }) => apiGet<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(applicationId)}`, signal),
    staleTime: 0,
  })) });
  const loaded = applications.every((query) => query.isSuccess);
  const current = applications.map((query) => query.data).filter((app): app is EmployerApplication => Boolean(app));
  const total = current.reduce((sum, app) => sum + app.credit_cost, 0);
  const limits = catalog.data?.admission_limits;
  const maxBudget = Number(budget);
  const validBudget = Number.isInteger(maxBudget) && maxBudget >= total && maxBudget <= (limits?.batch_credit_limit ?? 0);
  const duplicate = new Set(current.map((app) => app.opening_key)).size !== current.length;
  const quoteReady = loaded && current.length > 0 && !duplicate && current.length <= (limits?.batch_limit ?? 0) && validBudget && current.every((app) => batchEligibility(app, catalog.data, now) === null);
  const matches = batch.data?.items.every((item) => batchItemMatches(item, current.find((app) => app.id === item.application_id))) ?? false;
  const unexpired = Boolean(batch.data && Date.parse(batch.data.expires_at) > now && batch.data.items.every((item) => Date.parse(item.admission_snapshot.expires_at) > now));
  const allReviewed = Boolean(batch.data?.items.length && batch.data.items.every((item) => reviews[`${batch.data!.package_digest}:${item.application_id}:${item.package_digest}`]));
  const enabled = Boolean(catalog.data?.enabled && catalog.data.auto_submit_enabled);
  const routesAvailable = current.every((app) => app.application_mode === "api" && app.posting.application_mode === "api" && catalog.data?.sources.some((source) => source.id === app.posting.source_id && source.application_mode === "api"));
  const prepared = current.every((app) => ["ready", "approved"].includes(app.status) && !app.missing_fields.length && (!app.batch_id || app.batch_id === batchId));
  const boundApproval = current.every((app) => app.status === "approved" && app.batch_id === batchId && !app.missing_fields.length);
  const canApprove = enabled && routesAvailable && loaded && matches && prepared && unexpired && allReviewed && batch.data?.status === "quoted";
  const canQueue = enabled && routesAvailable && loaded && matches && boundApproval && unexpired && batch.data?.status === "approved" && (catalog.data?.balance ?? -1) >= (batch.data?.quoted_credits ?? Infinity);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  useEffect(() => {
    if (!batch.data) return;
    for (const [applicationId, status] of Object.entries(batch.data.application_statuses)) {
      const previous = polledStatuses.current[applicationId];
      if (previous && previous !== status) void client.invalidateQueries({ queryKey: ["employer-jobs", "application", applicationId], exact: true });
    }
    polledStatuses.current = batch.data.application_statuses;
  }, [batch.data, client]);

  function update(updated: EmployerApplicationBatch) {
    client.setQueryData(["employer-jobs", "batch", updated.id], updated);
    void client.invalidateQueries({ queryKey: ["employer-jobs", "application"] });
    onChange();
  }
  const quote = useMutation({
    mutationFn: () => {
      if (!quoteReady) throw new Error("Select complete, distinct API applications with current quotes and an explicit credit budget.");
      const payload = { items: current.map((app) => ({ application_id: app.id, package_digest: app.package_digest, allowed_actions: actions })), max_total_credits: maxBudget };
      const fingerprint = JSON.stringify(payload);
      if (intent.current?.fingerprint !== fingerprint) intent.current = { fingerprint, key: crypto.randomUUID() };
      return apiPostJson<EmployerApplicationBatch>(`${employerJobsBase}/application-batches`, { ...payload, idempotency_key: intent.current.key });
    },
    onSuccess: (value) => { client.setQueryData(["employer-jobs", "batch", value.id], value); setBatchId(value.id); setReviews({}); },
  });
  const approve = useMutation({ mutationFn: () => {
    if (!canApprove || !batch.data) throw new Error("Review every unchanged job, file, answer and action in this unexpired batch first.");
    return apiPostJson<EmployerApplicationBatch>(`${employerJobsBase}/application-batches/${encodeURIComponent(batch.data.id)}/approve`, { package_digest: batch.data.package_digest });
  }, onSuccess: update, onError: () => { setReviews({}); void batch.refetch(); void client.invalidateQueries({ queryKey: ["employer-jobs", "application"] }); } });
  const execute = useMutation({ mutationFn: () => {
    if (!canQueue || !batch.data) throw new Error("This exact approved batch must still be current, enabled and within your service credit balance.");
    return apiPostJson<EmployerApplicationBatch>(`${employerJobsBase}/application-batches/${encodeURIComponent(batch.data.id)}/execute`, { package_digest: batch.data.package_digest });
  }, onSuccess: update, onError: () => { void batch.refetch(); onChange(); } });
  const cancel = useMutation({ mutationFn: () => apiPostJson<EmployerApplicationBatch>(`${employerJobsBase}/application-batches/${encodeURIComponent(batchId!)}/cancel`, {}), onSuccess: update });
  const busy = quote.isPending || approve.isPending || execute.isPending || cancel.isPending;
  const error = quote.error || approve.error || execute.error || cancel.error;

  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/30 backdrop-blur-sm" />
    <Dialog.Content className="fixed inset-x-2 top-[3vh] z-[70] mx-auto flex max-h-[94dvh] max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-white shadow-xl sm:inset-x-6">
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-border p-5 sm:p-6"><div className="min-w-0"><Dialog.Title className="break-words font-display text-2xl sm:text-3xl">{batch.data?.status === "queued" || batch.data?.status === "cancelled" ? "Batch status" : "Review applications"}</Dialog.Title><Dialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">Only the jobs listed here are included. Approval permits the displayed actions; queueing is a separate step.</Dialog.Description></div><Dialog.Close asChild><Button variant="ghost" size="icon" className="min-h-11 min-w-11 shrink-0" disabled={busy} aria-label="Close batch review"><X size={20} /></Button></Dialog.Close></header>
      <div className="min-h-0 space-y-6 overflow-y-auto overscroll-contain p-5 sm:p-6">
        {batch.isError || applications.some((query) => query.isError) ? <div role="alert" className="space-y-3"><p className="text-sm text-coral">Could not load the exact batch or one of its applications. Approval and queueing are disabled.</p><Button variant="secondary" onClick={() => { void batch.refetch(); applications.forEach((query) => void query.refetch()); }}>Retry loading</Button></div> : !loaded || (batchId && !batch.data) ? <LoadingBlock rows={4} /> : null}
        {!batchId ? <section className="space-y-3 rounded-xl border border-border bg-surface p-4"><h2 className="font-semibold">Create a fixed batch quote</h2><p className="text-sm leading-6">{current.length} selected jobs · {total} service credits at their saved per-job prices. Creating the quote does not approve or submit anything.</p><label className="grid gap-2 text-sm font-semibold">Maximum service credit budget<input className="field-control" type="number" min={Math.max(1, total)} max={limits?.batch_credit_limit} step={1} value={budget} onChange={(event) => setBudget(event.target.value)} disabled={busy} /></label><p className="text-xs leading-5 text-muted-foreground">Up to {limits?.batch_limit ?? "—"} jobs and {limits?.batch_credit_limit ?? "—"} credits per batch. Unused budget is not charged. Service credits are separate from Premium and AI analysis units; optional tailoring has its own quote in Workspace.</p>{duplicate ? <p role="alert" className="text-sm text-coral">Select only one representation of each employer opening.</p> : null}{current.map((app) => { const reason = batchEligibility(app, catalog.data, now); return reason ? <p key={app.id} role="alert" className="text-sm text-coral">{app.posting.title}: {reason}</p> : null; })}<Button className="min-h-11 w-full sm:w-auto" disabled={!quoteReady || busy} onClick={() => quote.mutate()}>{quote.isPending ? <LoaderCircle size={16} className="animate-spin" /> : null}Create exact batch quote</Button></section> : null}
        {batch.data ? <section className="space-y-3 rounded-xl border border-border bg-surface p-4" aria-label="Fixed batch quote"><h2 className="font-semibold">{batch.data.items.length} exact jobs · {batch.data.quoted_credits} quoted service credits</h2><p className="text-sm">Maximum authorized budget: {batch.data.max_total_credits} service credits. Each confirmed complete application uses its displayed fixed price.</p><p className="text-xs leading-5">Batch quote expires {timestamp(batch.data.expires_at)}. Individual package quotes may expire earlier.</p><details className="text-xs"><summary className="min-h-11 cursor-pointer py-3 font-semibold">Application limits</summary><p className="leading-5 text-muted-foreground">Candidate limits: {batch.data.admission_snapshot.daily_limit} admissions per UTC day; {batch.data.admission_snapshot.rolling_limit} in {batch.data.admission_snapshot.rolling_days} days; {batch.data.admission_snapshot.pending_limit} pending or unknown. Limits and available credits are checked again when queueing and before disclosure. These are ceilings, not remaining capacity.</p></details><details className="text-xs"><summary className="min-h-11 cursor-pointer py-3 font-semibold">Batch digest and review link</summary><p className="break-all font-mono">{batch.data.package_digest}</p><Link className="mt-3 inline-block break-all text-primary underline" href={`/employer-jobs/batches/${encodeURIComponent(batch.data.id)}`}>Return to this exact batch</Link></details></section> : null}
        {batch.data && !unexpired && ["quoted", "approved"].includes(batch.data.status) ? <p role="alert" className="rounded-lg bg-amber-50 p-4 text-sm leading-6 text-amber-900">A quote expired. This batch cannot be approved or queued. Cancel it and save fresh individual packages before a new review.</p> : null}
        {batch.data && loaded && !matches ? <p role="alert" className="rounded-lg bg-amber-50 p-4 text-sm leading-6 text-amber-900">An application no longer matches the sealed batch. Its current contents are not the reviewed version. Approval and queueing are disabled; cancel remaining work and review a new package.</p> : null}
        {(batch.data?.items ?? current.map((app) => ({ application_id: app.id, package_digest: app.package_digest, credit_cost: app.credit_cost, allowed_actions: [...actions], opening_key: app.opening_key!, employer_key: app.employer_key!, pricing_snapshot: app.pricing_snapshot!, admission_snapshot: app.admission_snapshot! }))).map((item, index) => {
          const app = current.find((value) => value.id === item.application_id);
          const key = `${batch.data?.package_digest ?? "draft"}:${item.application_id}:${item.package_digest}`;
          return <BatchItemReview key={key} item={item} application={app} number={index + 1} status={batch.data?.application_statuses[item.application_id] ?? app?.status} reviewable={batch.data?.status === "quoted" && unexpired && !busy} onReview={(reviewed) => setReviews((previous) => ({ ...previous, [key]: reviewed }))} />;
        })}
        {error ? <p role="alert" className="text-sm leading-6 text-coral">{error.message} Refresh status before retrying an interrupted request. Queue acceptance is atomic; this screen never retries employer sends.</p> : null}
        {batch.data?.status === "quoted" ? <div className="space-y-3 border-t border-border pt-5"><p className="text-sm leading-6">Approval covers these exact files, destinations, answers, consent choices and actions. Filling or uploading may save data with an employer before final submission.</p><Button className="min-h-11 w-full sm:w-auto" disabled={!canApprove || busy} onClick={() => approve.mutate()}><ShieldCheck size={16} />Approve these {batch.data.items.length} exact applications</Button></div> : null}
        {batch.data?.status === "approved" ? <div className="space-y-3 rounded-xl border border-primary/30 bg-primary/5 p-4"><h2 className="font-semibold">Approved; not queued</h2><p className="text-sm leading-6">Queueing reserves {batch.data.quoted_credits} service credits and the admission for each listed job together. The server may reject the whole batch if a current limit or package changed.</p><Button className="min-h-11 w-full sm:w-auto" disabled={!canQueue || busy} onClick={() => execute.mutate()}>Queue these approved applications</Button>{(catalog.data?.balance ?? -1) < batch.data.quoted_credits ? <p className="text-sm">You need {batch.data.quoted_credits} service credits. <Link href="/billing" className="text-primary underline">Review credit packs</Link>.</p> : null}</div> : null}
        {batch.data?.status === "queued" ? <p role="status" className="text-sm leading-6">Batch accepted into the queue. Each job below has its own outcome; queue acceptance is not employer confirmation.</p> : null}
        {batch.data?.status === "cancelled" ? <p role="status" className="text-sm leading-6">Cancellation requested for remaining work. Check each job's outcome below; cancellation does not recall data already disclosed or prove an in-flight send did not happen.</p> : null}
        {batch.data && batch.data.status !== "cancelled" ? <div className="space-y-3 border-t border-border pt-5"><Button variant="danger" className="min-h-11" disabled={busy} onClick={() => cancel.mutate()}>Cancel remaining batch work</Button><p className="text-xs leading-5 text-muted-foreground">Confirmed applications remain confirmed. Unknown or in-flight sends may remain with the employer and retain their admission and credit holds until independently resolved.</p></div> : null}
        {batchId ? <Button variant="ghost" className="min-h-11" disabled={busy} onClick={() => { void batch.refetch(); applications.forEach((query) => void query.refetch()); }}><RefreshCw size={16} />Refresh batch status</Button> : null}
        {!enabled ? <p className="text-sm leading-6 text-muted-foreground">Reviewed API submission is disabled for this service. The current manual sources continue through individual employer portals.</p> : null}
        {enabled && loaded && !routesAvailable ? <p role="alert" className="text-sm leading-6 text-coral">A listed employer no longer offers a current permissioned API route. Approval and queueing are disabled.</p> : null}
      </div>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}

function BatchItemReview({ item, application, number, status, reviewable, onReview }: { item: EmployerBatchItem; application: EmployerApplication | undefined; number: number; status?: EmployerApplication["status"]; reviewable: boolean; onReview: (value: boolean) => void }) {
  const [artifactReady, setArtifactReady] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const docx = application?.artifact?.media_type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  const previewReady = useCallback((ready: boolean) => { if (!docx) setArtifactReady(ready); }, [docx]);
  const matching = batchItemMatches(item, application);
  const destination = safeEmployerUrl(application?.posting.apply_url);
  async function download() {
    setDownloadError(null);
    try { await apiDownload(`${employerJobsBase}/applications/${encodeURIComponent(item.application_id)}/artifact`, application?.artifact?.filename ?? "review-resume"); if (docx) setArtifactReady(true); }
    catch (error) { setDownloadError(error instanceof Error ? error.message : "Could not download the exact file."); }
  }
  return <article className="min-w-0 space-y-4 rounded-xl border border-border p-4 sm:p-5" aria-label={`Batch job ${number}`}>
    <div><p className="text-xs font-semibold text-primary">Job {number} · {application?.posting.employer ?? item.employer_key}</p><h2 className="mt-1 break-words text-xl font-semibold">{application?.posting.title ?? item.application_id}</h2><p className="mt-2 text-sm font-semibold">{status ? applicationStatusLabels[status] : "Loading status…"}</p>{destination ? <a className="mt-2 block break-all text-xs text-primary underline" href={destination} target="_blank" rel="noopener noreferrer">{destination}</a> : null}</div>
    <p className="text-sm">{item.credit_cost} service credits per confirmed complete application.</p><p className="text-sm leading-6">Authorized actions: {item.allowed_actions.map((action) => ({ fill: "fill these answers", upload: "upload this file", submit: "submit this application" })[action]).join("; ")}.</p>
    {status === "unknown" ? <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm leading-6 text-amber-900">The employer may have received this application. No automatic repeat send is available. Its credit and admission holds remain pending independent resolution.</p> : status === "confirmed" ? <p className="text-sm leading-6 text-primary">Employer-confirmed complete submission. {application?.charged_credits ?? item.credit_cost} service credits charged.</p> : status === "queued" || status === "submitting" ? <p className="text-sm leading-6">{application?.reserved_credits ?? item.credit_cost} service credits reserved. Waiting for an employer-confirmed outcome.</p> : null}
    {application?.cancel_requested ? <p className="text-xs leading-5">Cancellation requested. This does not recall an in-flight send.</p> : null}
    {!matching ? <p role="alert" className="text-sm leading-6 text-coral">The current package does not match this batch item. Its contents cannot be used for approval.</p> : application ? <>
      <p className="text-xs leading-5 text-muted-foreground">Resume choice: {application.resume_choice === "tailored" ? "Approved tailored version" : application.resume_choice === "custom" ? "Custom file" : "Original file"}. Change the choice or answers in the individual application before creating a new batch.</p>
      <div><h3 className="font-semibold">Exact file to disclose</h3><p className="mt-2 text-xs text-muted-foreground">{docx ? "DOCX" : "PDF"}</p><Button variant="secondary" className="my-3 min-h-11" disabled={!application.artifact} onClick={() => void download()}><Download size={16} />Download exact file for job {number}</Button><ArtifactPreview application={application} onReady={previewReady} />{downloadError ? <p role="alert" className="mt-2 text-sm text-coral">{downloadError}</p> : null}</div>
      <div><h3 className="font-semibold">Exact application answers</h3><dl className="mt-3 grid gap-3 text-sm">{application.form.fields.filter((field) => !["file", "consent"].includes(field.type)).map((field) => { const value = application.answers[field.id]; const values = Array.isArray(value) ? value : [value ?? ""]; return <div key={field.id}><dt className="font-semibold">{field.label}{field.type === "hidden" ? " (employer metadata)" : ""}{field.required ? " (required)" : ""}</dt><dd className="mt-1 whitespace-pre-wrap break-words">{values.map((answer) => field.options.find((option) => option.value === answer)?.label ?? answer).join(", ") || "No answer provided"}</dd></div>; })}</dl>{!application.form.fields.length ? <p className="text-sm">No field contract available.</p> : null}</div>
      <div><h3 className="font-semibold">Exact employer consent choices</h3>{application.form.consents.map((consent) => <div className="mt-3 text-sm leading-6" key={consent.id}><p className="font-semibold">{consent.label}: {application.consents[consent.id] ? "Agreed" : "Not agreed"}{consent.required ? " (required)" : " (optional)"}</p>{consent.text ? <p className="mt-1 whitespace-pre-wrap break-words text-xs">{consent.text}</p> : null}{safeEmployerUrl(consent.policy_url) ? <a className="text-primary underline" href={safeEmployerUrl(consent.policy_url)!} target="_blank" rel="noopener noreferrer">Read employer policy</a> : null}</div>)}{!application.form.consents.length ? <p className="mt-2 text-sm">No separate consent choices in this form.</p> : null}</div>
      {item.admission_snapshot ? <p className="text-xs leading-5 text-muted-foreground">Employer limit: {item.admission_snapshot.employer.daily_limit} per UTC day and {item.admission_snapshot.employer.rolling_limit} in {item.admission_snapshot.employer.rolling_days} days. {item.admission_snapshot.employer.evidence_note}{safeEmployerUrl(item.admission_snapshot.employer.evidence_url ?? undefined) ? <> <a className="text-primary underline" href={safeEmployerUrl(item.admission_snapshot.employer.evidence_url ?? undefined)!} target="_blank" rel="noopener noreferrer">Policy evidence</a></> : null}</p> : null}
      <details className="text-xs"><summary className="min-h-11 cursor-pointer py-3 font-semibold">Package details</summary><p className="break-all">File SHA-256: {application.artifact?.sha256}</p><p className="mt-2 break-all">Price version: {item.pricing_snapshot?.pricing_version ?? "unavailable"}</p><p className="mt-2 break-all">Form version: {application.form.version}</p><p className="mt-2 break-all">Employer policy version: {item.admission_snapshot?.employer.version}</p><p className="mt-2 break-all">Package: {item.package_digest}</p><p className="mt-2 break-all">Opening: {item.opening_key}</p></details>
      {reviewable ? <label className="flex min-h-11 items-start gap-3 text-sm leading-6"><input className="mt-1 h-4 w-4 shrink-0 accent-primary" type="checkbox" checked={reviewed} disabled={!artifactReady} onChange={(event) => { setReviewed(event.target.checked); onReview(event.target.checked); }} /><span>I reviewed job {number}, this employer destination, exact file, every answer and consent choice, fixed price and listed actions.</span></label> : null}
      {reviewable && docx && !artifactReady ? <p className="text-xs leading-5 text-muted-foreground">Download and review the DOCX in your document editor before marking this job reviewed.</p> : null}
    </> : <LoadingBlock rows={2} />}
  </article>;
}
