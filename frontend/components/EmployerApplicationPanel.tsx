"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Download, LoaderCircle, RefreshCw, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { apiBlob, apiDownload, apiGet, apiPostForm, apiPostJson, apiPutJson } from "../lib/api";
import { getSourcePreservingContent, type Opportunity, type ResumeVersion } from "../lib/career";
import { applicationStatusLabels, employerJobsBase, safeEmployerUrl, type EmployerApplication, type JobServiceCatalog, type ResumeChoice } from "../lib/employerJobs";
import type { ResumeListResponse, ResumeParseResponse } from "../lib/types";
import { Button } from "./ui/Button";
import { LoadingBlock } from "./ui/LoadingBlock";

type Props = { id: string; onClose: () => void; onChange: () => void; resumes: ResumeListResponse["resumes"] };

export function ArtifactPreview({ application, onReady }: { application: EmployerApplication; onReady: (ready: boolean) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const artifact = application.artifact;
  const mediaType = artifact?.media_type;
  const sha = artifact?.sha256;
  const pdf = mediaType === "application/pdf";
  const docx = mediaType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  useEffect(() => {
    onReady(false);
    if (!sha) return;
    if (docx) { onReady(true); return; }
    if (!pdf) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    apiBlob(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/artifact`, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        if (blob.type.split(";")[0] !== "application/pdf") throw new Error("The preview did not return a PDF. Refresh the package before approving.");
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        onReady(true);
      })
      .catch((previewError: unknown) => { if (!controller.signal.aborted) setError(previewError instanceof Error ? previewError.message : "Could not load the exact application file."); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [application.id, sha, onReady, pdf, docx, attempt]);
  if (!artifact) return <p className="text-sm text-coral">Choose a retained original file or an approved tailored version before reviewing.</p>;
  return <div className="space-y-3">
    <div><p className="break-words text-sm font-semibold">{artifact.filename}</p><p className="mt-1 text-xs text-muted-foreground">{(artifact.size_bytes / 1024).toFixed(1)} KB · Exact sealed application file</p></div>
    {pdf ? error ? <div><p role="alert" className="text-sm text-coral">{error}</p><Button variant="secondary" size="sm" className="mt-2" onClick={() => { setError(null); setUrl(null); setAttempt((value) => value + 1); }}>Retry exact file preview</Button></div> : url ? <iframe title="Exact resume for this application" src={url} className="h-[420px] w-full rounded-lg border border-border bg-surface sm:h-[560px]" /> : <LoadingBlock rows={3} /> : docx ? <p className="text-sm leading-6 text-muted-foreground">Download this DOCX and review it in your document editor. Browser conversion is not used.</p> : <p role="alert" className="text-sm text-coral">This file format is not supported for review. Submission is disabled.</p>}
  </div>;
}

export default function EmployerApplicationPanel({ id, onClose, onChange, resumes }: Props) {
  const application = useQuery({
    queryKey: ["employer-jobs", "application", id],
    queryFn: ({ signal }) => apiGet<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(id)}`, signal),
    staleTime: 0,
    refetchInterval: (query) => ["queued", "submitting"].includes(query.state.data?.status ?? "") ? 2000 : false,
  });
  const status = application.data?.status;
  useEffect(() => {
    if (status && ["confirmed", "failed", "cancelled"].includes(status)) onChange();
  }, [status, onChange]);
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-[60] bg-black/30 backdrop-blur-sm" /><Dialog.Content className="fixed inset-x-2 top-[3vh] z-[70] mx-auto flex max-h-[94dvh] w-auto max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-white shadow-xl sm:inset-x-6 sm:top-[5vh] sm:max-h-[90dvh]">
    <header className="flex shrink-0 items-start justify-between gap-4 border-b border-border p-5 sm:p-6"><div className="min-w-0"><Dialog.Title className="font-display text-2xl sm:text-3xl">Review your application</Dialog.Title><Dialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">Choose the exact file and answers before anything is shared with the employer.</Dialog.Description></div><Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="Close application review"><X size={20} /></Button></Dialog.Close></header>
    <div className="min-h-0 overflow-y-auto overscroll-contain p-5 sm:p-6">
      {application.isError ? <div role="alert" className="space-y-3"><p className="text-sm text-coral">{application.error.message}</p><Button variant="secondary" onClick={() => void application.refetch()}>Retry loading</Button></div> : application.isLoading || !application.data ? <LoadingBlock rows={5} /> : <ApplicationEditor key={`${id}:${application.data.package_digest}`} application={application.data} onChange={onChange} resumes={resumes} />}
    </div>
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}

function ApplicationEditor({ application, onChange, resumes }: { application: EmployerApplication; onChange: () => void; resumes: ResumeListResponse["resumes"] }) {
  const client = useQueryClient();
  const [resumeId, setResumeId] = useState(String(application.resume_id));
  const [choice, setChoice] = useState<ResumeChoice>(application.resume_choice);
  const [versionId, setVersionId] = useState(application.resume_version_id ?? "");
  const [answers, setAnswers] = useState(application.answers);
  const [consents, setConsents] = useState(application.consents);
  const [reviewed, setReviewed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [artifactReady, setArtifactReady] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const catalog = useQuery({ queryKey: ["employer-jobs", "catalog"], queryFn: ({ signal }) => apiGet<JobServiceCatalog>(`${employerJobsBase}/catalog`, signal) });
  const versions = useQuery({ queryKey: ["resume-versions", resumeId], queryFn: ({ signal }) => apiGet<ResumeVersion[]>(`/v1/resume-versions?resume_id=${encodeURIComponent(resumeId)}`, signal), enabled: choice === "tailored" && Boolean(resumeId) });
  const immutable = ["queued", "submitting", "confirmed", "unknown", "cancelled"].includes(application.status);
  const automatic = application.application_mode === "api";
  const approvedVersions = (versions.data ?? []).filter((version) => version.approval_state === "approved" && Boolean(getSourcePreservingContent(version.structured_content)?.source_edits.length));
  const employerUrl = safeEmployerUrl(application.posting.apply_url || application.posting.canonical_url);
  const retainedResumes = resumes.filter((resume) => resume.source_available);
  const busy = application.status === "queued" || application.status === "submitting";

  function update(updated: EmployerApplication) {
    client.setQueryData(["employer-jobs", "application", application.id], updated);
    void client.invalidateQueries({ queryKey: ["employer-jobs", "applications"] });
    onChange();
  }
  const tailoringWorkspace = useMutation({
    mutationFn: () => apiPostJson<Opportunity>("/v1/opportunities", { title: application.posting.title, company: application.posting.employer, location: application.posting.location, source_url: application.posting.canonical_url, job_description: application.posting.description, source: "employer_search", resume_id: Number(resumeId), priority: "medium" }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["opportunities"] }); },
  });
  const save = useMutation({ mutationFn: () => apiPutJson<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/package`, { resume_id: Number(resumeId), resume_choice: choice, resume_version_id: choice === "tailored" ? versionId : null, answers, consents }), onSuccess: (updated) => { setDirty(false); setReviewed(false); update(updated); } });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const formats = ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"];
      if (!formats.includes(file.type) || file.size > 5 * 1024 * 1024) throw new Error("Choose a PDF or DOCX no larger than 5 MB.");
      const form = new FormData(); form.append("file", file);
      return apiPostForm<ResumeParseResponse>("/resume/parse", form);
    },
    onSuccess: (result) => { setResumeId(String(result.resume_id)); setChoice("custom"); setVersionId(""); setDirty(true); setReviewed(false); void client.invalidateQueries({ queryKey: ["resumes"] }); setFileError(null); },
    onError: (error) => setFileError(error.message),
  });
  const submit = useMutation({
    mutationFn: async () => {
      if (!reviewed || dirty || !artifactReady || !automatic || application.batch_id || !catalog.data?.auto_submit_enabled || application.missing_fields.length) throw new Error("Save and review the complete application before submitting. Batch members must be queued from their exact batch review.");
      const approved = await apiPostJson<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/approve`, { package_digest: application.package_digest, allowed_actions: ["fill", "upload", "submit"] });
      client.setQueryData(["employer-jobs", "application", application.id], approved);
      return apiPostJson<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/execute`, { package_digest: approved.package_digest });
    },
    onSuccess: update,
    onError: () => { void client.invalidateQueries({ queryKey: ["employer-jobs", "application", application.id] }); onChange(); },
  });
  const handoff = useMutation({
    mutationFn: async () => {
      if (automatic || !reviewed || dirty || !artifactReady || !employerUrl || application.batch_id || application.missing_fields.length) throw new Error("Save and review the exact handoff package before continuing.");
      const approved = await apiPostJson<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/approve`, { package_digest: application.package_digest, allowed_actions: ["fill", "upload"] });
      client.setQueryData(["employer-jobs", "application", application.id], approved);
      return apiPostJson<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/execute`, { package_digest: approved.package_digest });
    },
    onSuccess: update,
    onError: () => { void client.invalidateQueries({ queryKey: ["employer-jobs", "application", application.id] }); onChange(); },
  });
  const cancel = useMutation({ mutationFn: () => apiPostJson<EmployerApplication>(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/cancel`, {}), onSuccess: update });
  const pending = save.isPending || upload.isPending || submit.isPending || handoff.isPending || cancel.isPending;
  const error = save.error || submit.error || handoff.error || cancel.error;
  const creditBalance = catalog.data?.balance;
  const enoughCredits = typeof creditBalance === "number" && creditBalance >= application.credit_cost;

  async function downloadArtifact() {
    setDownloadError(null);
    try { await apiDownload(`${employerJobsBase}/applications/${encodeURIComponent(application.id)}/artifact`, application.artifact?.filename ?? "application-resume"); }
    catch (error) { setDownloadError(error instanceof Error ? error.message : "Could not download the exact application file."); }
  }
  function markDirty() { setDirty(true); setReviewed(false); }
  return <div className="space-y-7">
    <section className="border-b border-border pb-5"><p className="text-xs font-semibold text-primary">{application.posting.employer} · {application.posting.platform}</p><h2 className="mt-2 break-words text-xl font-semibold">{application.posting.title}</h2><p className="mt-2 text-sm text-muted-foreground">{application.posting.location}</p><p className="mt-3 inline-flex items-center gap-2 text-sm font-semibold text-primary"><ShieldCheck size={16} />{applicationStatusLabels[application.status]}</p>{employerUrl && (automatic || (application.status === "manual_handoff" && !dirty)) ? <a href={employerUrl} target="_blank" rel="noopener noreferrer" className="mt-3 block break-all text-xs text-primary underline underline-offset-4">{employerUrl}</a> : employerUrl ? <p className="mt-3 break-all text-xs text-muted-foreground">{employerUrl}</p> : <p className="mt-3 text-sm text-coral">Employer destination unavailable. Submission is disabled.</p>}</section>
    {application.status === "confirmed" ? <section className="rounded-lg border border-primary/30 bg-primary/5 p-4" aria-live="polite"><h3 className="flex items-center gap-2 font-semibold"><CheckCircle2 size={18} />Employer submission confirmed</h3><p className="mt-2 text-sm">{application.charged_credits} service credits charged.</p>{application.receipt ? <dl className="mt-3 grid gap-2 text-xs">{Object.entries(application.receipt).filter(([, value]) => ["string", "number"].includes(typeof value)).map(([key, value]) => <div key={key}><dt className="font-semibold">{key.replaceAll("_", " ")}</dt><dd className="break-all">{String(value)}</dd></div>)}</dl> : null}</section> : null}
    {application.status === "unknown" ? <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">The employer may have received this application. Do not send another copy until its status is confirmed. No automatic retry is available.</div> : null}
    <p className="text-xs leading-5 text-muted-foreground">{application.reserved_credits ?? 0} service credits currently reserved · {application.charged_credits} charged.</p>
    {application.error ? <p role="alert" className="text-sm text-coral">{application.error.message}</p> : null}
    <section aria-labelledby="application-resume-heading"><h3 id="application-resume-heading" className="font-semibold">Resume for this job</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">Switch to the original or a custom file at any time before submission. Saving a change clears the previous approval.</p><div className="mt-4 grid gap-4 sm:grid-cols-2">
      <label className="grid gap-2 text-sm font-semibold">Resume choice<select className="field-control" value={choice} disabled={immutable || pending} onChange={(event) => { setChoice(event.target.value as ResumeChoice); setVersionId(""); markDirty(); }}><option value="original">Original resume</option><option value="tailored">Approved tailored version</option><option value="custom">Custom upload</option></select></label>
      <label className="grid gap-2 text-sm font-semibold">Saved original file<select className="field-control min-w-0" value={resumeId} disabled={immutable || pending} onChange={(event) => { setResumeId(event.target.value); setVersionId(""); markDirty(); }}><option value="">Choose retained source</option>{retainedResumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename} · #{resume.id}</option>)}{resumeId && !retainedResumes.some((resume) => String(resume.id) === resumeId) ? <option value={resumeId}>Selected file · #{resumeId}</option> : null}</select></label>
      {choice === "tailored" ? <label className="grid gap-2 text-sm font-semibold sm:col-span-2">Approved version<select className="field-control" value={versionId} disabled={immutable || pending || versions.isLoading} onChange={(event) => { setVersionId(event.target.value); markDirty(); }}><option value="">Choose a reviewed version</option>{approvedVersions.map((version) => <option key={version.id} value={version.id}>Version {version.version_number}: {version.label}</option>)}</select>{versions.isError ? <span role="alert" className="text-xs text-coral">Could not load versions. <button type="button" className="underline" onClick={() => void versions.refetch()}>Retry</button></span> : !versions.isLoading && !approvedVersions.length ? <span className="text-xs font-normal leading-5 text-muted-foreground">No source-preserving version has been approved for this resume. Prepare tailoring below, approve its exact native file in Workspace, then return here and choose it.</span> : null}</label> : null}
      {choice === "custom" ? <label className="grid gap-2 text-sm font-semibold sm:col-span-2">Upload a custom PDF or DOCX<input type="file" accept="application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="field-control text-xs" disabled={immutable || pending} onChange={(event) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); }} />{upload.isPending ? <span className="text-xs font-normal">Saving your original file…</span> : null}</label> : null}
    </div>{choice === "tailored" && !immutable ? <div className="mt-4 space-y-2">{tailoringWorkspace.data ? <Link href={`/workspace/${encodeURIComponent(tailoringWorkspace.data.id)}?tab=resume`} className="inline-flex text-sm font-semibold text-primary underline underline-offset-4">Review tailoring for this job in Workspace</Link> : <Button variant="secondary" size="sm" disabled={!resumeId || pending || tailoringWorkspace.isPending} onClick={() => tailoringWorkspace.mutate()}>Prepare tailored resume for this job</Button>}<p className="text-xs leading-5 text-muted-foreground">This saves the employer job and chosen resume in Workspace. Generate and approve the tailored version there; this action does not submit an application.</p>{tailoringWorkspace.error ? <p role="alert" className="text-sm text-coral">{tailoringWorkspace.error.message}</p> : null}</div> : null}{fileError ? <p role="alert" className="mt-3 text-sm text-coral">{fileError}</p> : null}</section>
    <section aria-labelledby="application-answers-heading"><h3 id="application-answers-heading" className="font-semibold">Application answers</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">Check every value. Missing answers and new requirements pause the application; personal declarations are never guessed.</p><div className="mt-4 grid gap-4">{application.form.fields.filter((field) => !["hidden", "file", "consent"].includes(field.type)).map((field) => {
      const fieldId = `application-field-${field.id}`;
      const value = answers[field.id] ?? "";
      return <div key={field.id}><label htmlFor={field.type === "multi_select" ? undefined : fieldId} className="mb-2 block text-sm font-semibold">{field.label}{field.required ? " (required)" : ""}</label>{field.type === "single_select" ? <select id={fieldId} className="field-control" value={typeof value === "string" ? value : ""} disabled={immutable || pending} onChange={(event) => { setAnswers((current) => ({ ...current, [field.id]: event.target.value })); markDirty(); }}><option value="">Choose an answer</option>{field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select> : field.type === "multi_select" ? <fieldset className="grid gap-2"><legend className="sr-only">{field.label}</legend>{field.options.map((option) => <label key={option.value} className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 accent-primary" checked={Array.isArray(value) && value.includes(option.value)} disabled={immutable || pending} onChange={(event) => { const existing = Array.isArray(value) ? value : []; setAnswers((current) => ({ ...current, [field.id]: event.target.checked ? [...existing, option.value] : existing.filter((item) => item !== option.value) })); markDirty(); }} />{option.label}</label>)}</fieldset> : field.type === "textarea" ? <textarea id={fieldId} className="field-control min-h-28" value={typeof value === "string" ? value : ""} disabled={immutable || pending} onChange={(event) => { setAnswers((current) => ({ ...current, [field.id]: event.target.value })); markDirty(); }} /> : <input id={fieldId} className="field-control" type={field.type === "email" ? "email" : "text"} value={typeof value === "string" ? value : ""} disabled={immutable || pending} onChange={(event) => { setAnswers((current) => ({ ...current, [field.id]: event.target.value })); markDirty(); }} />}</div>;
    })}</div>{!application.form.fields.length ? <p className="mt-3 text-sm text-muted-foreground">This employer requires its hosted application flow. Answers and required steps must be completed on that portal.</p> : null}</section>
    {application.form.consents.length ? <section aria-labelledby="consents-heading"><h3 id="consents-heading" className="font-semibold">Employer consent choices</h3><div className="mt-4 grid gap-4">{application.form.consents.map((consent) => <label key={consent.id} className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={consents[consent.id] === true} disabled={immutable || pending} onChange={(event) => { setConsents((current) => ({ ...current, [consent.id]: event.target.checked })); markDirty(); }} /><span>{consent.label}{consent.required ? " (required)" : " (optional)"}{consent.text ? <span className="mt-1 block text-xs text-muted-foreground">{consent.text}</span> : null}{safeEmployerUrl(consent.policy_url) ? <a href={safeEmployerUrl(consent.policy_url)!} target="_blank" rel="noopener noreferrer" className="ml-2 text-primary underline">Read policy</a> : null}</span></label>)}</div></section> : null}
    {!immutable ? <div><Button variant="secondary" onClick={() => save.mutate()} disabled={pending || !dirty || !resumeId || (choice === "tailored" && !versionId)}>{save.isPending ? <LoaderCircle size={16} className="animate-spin" /> : null}Save and refresh review</Button>{dirty ? <p className="mt-2 text-xs text-muted-foreground">Save your changes to refresh the exact file and validation before approval.</p> : null}</div> : null}
    <section className="border-t border-border pt-5" aria-labelledby="exact-file-heading"><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h3 id="exact-file-heading" className="font-semibold">Exact file to disclose</h3><Button variant="secondary" size="sm" onClick={() => void downloadArtifact()} disabled={!application.artifact || dirty}><Download size={14} />Download review file</Button></div>{dirty ? <p className="text-sm text-muted-foreground">Your saved preview is out of date. Save the package to preview the new file.</p> : <ArtifactPreview key={application.artifact?.sha256 ?? "missing"} application={application} onReady={setArtifactReady} />}{downloadError ? <p role="alert" className="mt-3 text-sm text-coral">{downloadError}</p> : null}</section>
    {application.missing_fields.length ? <div role="status" className="rounded-lg bg-amber-50 p-4 text-sm leading-6 text-amber-900">Needs attention: {application.missing_fields.join(", ")}. Complete the answers and save before submitting.</div> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-coral">{error.message}</p> : null}
    <section className="rounded-xl border border-border bg-surface p-5" aria-labelledby="application-submit-heading"><h3 id="application-submit-heading" className="font-semibold">{automatic ? "Approve this application" : "Continue with the employer"}</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{automatic ? `${application.credit_cost} service credits per confirmed complete application. Your file and answers may be saved by the employer as soon as filling or uploading begins.` : "This route uses the employer portal. Preparation is not a submission, and this handoff does not charge automatic-apply credits. Sign in, complete the remaining steps and submit there yourself."}</p>
      {application.batch_id ? <div className="mt-4 space-y-2"><p className="text-sm leading-6">This application belongs to an exact reviewed batch. Queue it from that batch review. Saving a file or answer change clears this package's approval and requires a new batch review.</p><Link href={`/employer-jobs/batches/${encodeURIComponent(application.batch_id)}`} className="inline-flex min-h-11 items-center text-sm font-semibold text-primary underline">Review this application's batch</Link></div> : automatic && !immutable ? <><label className="mt-4 flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={reviewed} disabled={dirty || !artifactReady || pending || application.missing_fields.length > 0} onChange={(event) => setReviewed(event.target.checked)} /><span>I reviewed this employer, exact resume, answers and consent choices. I authorize filling, uploading and submitting this application.</span></label><Button className="mt-4 min-h-11 w-full sm:w-auto" onClick={() => submit.mutate()} disabled={!reviewed || dirty || !artifactReady || pending || !enoughCredits || !employerUrl || !catalog.data?.auto_submit_enabled || application.missing_fields.length > 0}>{submit.isPending ? <LoaderCircle size={16} className="animate-spin" /> : <ShieldCheck size={16} />}Approve and submit</Button>{!catalog.data?.auto_submit_enabled ? <p className="mt-3 text-xs text-muted-foreground">Automatic submission is not enabled for this service.</p> : !enoughCredits ? <p className="mt-3 text-xs text-muted-foreground">You need {application.credit_cost} service credits. <Link href="/billing?sku=job_service_500" className="underline text-primary">Review credit packs</Link>.</p> : null}</> : !automatic && employerUrl && !immutable ? <div className="mt-4 space-y-4">
        <dl className="space-y-2 text-xs leading-5" aria-label="Saved handoff package">
          <div><dt className="font-semibold">Chosen file</dt><dd className="break-all">{application.artifact?.filename ?? "Unavailable"} · {application.resume_choice} resume #{application.resume_id}{application.resume_version_id ? ` · version ${application.resume_version_id}` : ""}</dd></div>
          <div><dt className="font-semibold">Exact file SHA-256</dt><dd className="break-all">{application.artifact?.sha256 ?? "Unavailable"}</dd></div>
          <div><dt className="font-semibold">Destination</dt><dd className="break-all">{employerUrl}</dd></div>
          <div><dt className="font-semibold">Saved answers</dt><dd>{Object.entries(application.answers).length ? <ul className="mt-1 space-y-1">{Object.entries(application.answers).map(([id, value]) => <li key={id} className="break-words"><span className="font-semibold">{application.form.fields.find((field) => field.id === id)?.label ?? id}: </span>{Array.isArray(value) ? value.join(", ") : value || "No answer"}</li>)}</ul> : "No answers saved; complete the employer form yourself."}</dd></div>
          <div><dt className="font-semibold">Handoff actions</dt><dd>Record reviewed fill/upload preparation only. No upload, autofill or send is performed. Final submission stays under your control. No automatic-application fee.</dd></div>
        </dl>
        {dirty ? <p className="text-xs text-muted-foreground">These are the saved details. Save changes and review the refreshed package.</p> : null}
        {application.status === "manual_handoff" && !dirty ? <><p role="status" className="text-sm font-semibold text-primary">Reviewed handoff prepared. This application has not been submitted.</p><Button asChild className="min-h-11"><a href={employerUrl} target="_blank" rel="noopener noreferrer">Open employer application<ArrowUpRight size={15} /></a></Button></> : <><label className="flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={reviewed} disabled={dirty || !artifactReady || pending || application.missing_fields.length > 0} onChange={(event) => setReviewed(event.target.checked)} /><span>I reviewed the saved destination, exact file, answers and consent choices. Prepare my manual handoff only; do not upload, autofill or submit.</span></label><Button className="min-h-11 w-full sm:w-auto" onClick={() => handoff.mutate()} disabled={!reviewed || dirty || !artifactReady || pending || application.missing_fields.length > 0}>{handoff.isPending ? <LoaderCircle size={16} className="animate-spin" /> : <ShieldCheck size={16} />}Approve and prepare handoff</Button></>}
      </div> : null}
      {busy ? <p role="status" className="mt-3 flex items-center gap-2 text-sm"><LoaderCircle size={16} className="animate-spin" />Waiting for an employer-confirmed result. You can close this review and return later.</p> : null}
    </section>
    {!["confirmed", "cancelled"].includes(application.status) ? <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4"><Button variant="danger" size="sm" onClick={() => cancel.mutate()} disabled={pending || application.status === "unknown"}>Cancel remaining work</Button><p className="max-w-xl text-xs leading-5 text-muted-foreground">Cancellation stops future work. Already disclosed data or an in-flight submission may remain with the employer.</p></div> : null}
    <Button variant="ghost" size="sm" onClick={() => void client.invalidateQueries({ queryKey: ["employer-jobs", "application", application.id] })}><RefreshCw size={14} />Refresh application status</Button>
  </div>;
}
