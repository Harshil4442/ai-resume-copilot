"use client";

import { AlertCircle, ArrowRight, CheckCircle2, FileText, FileUp, ShieldCheck, Target } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "../../components/ui/Button";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { trackEvent } from "../../lib/analytics";
import { apiPatchJson, apiPostForm } from "../../lib/api";
import type { Opportunity, OpportunityDetail } from "../../lib/career";
import type { ResumeParseResponse } from "../../lib/types";

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ACCEPTED_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

function ResumeUploadContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedOpportunity = searchParams.get("opportunity");
  const opportunityId = requestedOpportunity && /^opp_[A-Za-z0-9_-]{1,60}$/.test(requestedOpportunity) ? requestedOpportunity : null;
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [data, setData] = useState<ResumeParseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [enrichSkills, setEnrichSkills] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  function chooseFile(candidate: File | null) {
    if (loading || connecting) return;
    setData(null);
    setError(null);
    setConnectionError(null);
    if (!candidate) {
      setFile(null);
      return;
    }
    if (!ACCEPTED_TYPES.has(candidate.type) || candidate.size > MAX_FILE_BYTES) {
      setFile(null);
      setError("Choose a PDF or DOCX file no larger than 5 MB.");
      return;
    }
    setFile(candidate);
    trackEvent("resume_upload_selected", { file_type: candidate.type, size_bytes: candidate.size });
  }

  function handleDrag(event: React.DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(event.type === "dragenter" || event.type === "dragover");
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    chooseFile(event.dataTransfer.files?.[0] || null);
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError(null);
    setConnectionError(null);
    setData(null);
    setLoading(true);
    trackEvent("resume_upload_started", { file_type: file.type, size_bytes: file.size });
    const form = new FormData();
    form.append("file", file);
    form.append("enrich_skills", String(enrichSkills));
    try {
      const parsed = await apiPostForm<ResumeParseResponse>("/resume/parse", form);
      setData(parsed);
      await queryClient.invalidateQueries({ queryKey: ["resumes"] });
      trackEvent("resume_upload_completed", {
        resume_id: parsed.resume_id,
        skill_count: parsed.skills.length,
      });
      window.dispatchEvent(new Event("refresh_analysis_units"));
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Failed to parse resume.");
      trackEvent("resume_upload_failed", { file_type: file.type });
    } finally {
      setLoading(false);
    }
  }

  async function connectUploadedResume() {
    if (!opportunityId || !data?.source_available || !data.source_format) return;
    setConnectionError(null);
    setConnecting(true);
    try {
      const updated = await apiPatchJson<Opportunity>(`/v1/opportunities/${encodeURIComponent(opportunityId)}`, { resume_id: data.resume_id });
      await queryClient.cancelQueries({ queryKey: ["opportunity", opportunityId] });
      queryClient.setQueryData<OpportunityDetail>(["opportunity", opportunityId], (cached) => cached ? { ...cached, ...updated } : undefined);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] }),
        queryClient.invalidateQueries({ queryKey: ["opportunities"] }),
        queryClient.invalidateQueries({ queryKey: ["opportunity-match", opportunityId] }),
        queryClient.invalidateQueries({ queryKey: ["resumes"] }),
      ]);
      router.push(`/workspace/${encodeURIComponent(opportunityId)}?tab=resume`);
    } catch (connectionFailure) {
      setConnectionError(connectionFailure instanceof Error ? connectionFailure.message : "Could not connect this resume to your opportunity.");
    } finally {
      setConnecting(false);
    }
  }

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="grid gap-6 border-b border-border pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="eyebrow">Resume evidence</p>
            <h1 className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Add your source resume</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">HireWiz keeps your original file privately and extracts a working copy. Approve the facts to use, then review changes to existing text before downloading in the same file format.</p>
            {opportunityId ? <div className="mt-4 text-sm leading-6 text-muted-foreground"><p>Upload your original resume, then use it for the opportunity you came from. Import and approve the new upload&apos;s facts to enable tailoring.</p><Link href={`/workspace/${encodeURIComponent(opportunityId)}?tab=resume`} className="mt-2 inline-flex font-semibold text-primary hover:underline">Back to your opportunity</Link></div> : null}
          </div>
          <div className="flex gap-5 text-xs text-muted-foreground">
            <span className="flex items-center gap-2"><ShieldCheck size={16} className="text-primary" /> PDF or DOCX</span>
            <span>5 MB maximum</span>
          </div>
        </header>

        <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_340px]">
          <form onSubmit={onSubmit}>
            <label
              className={`relative flex min-h-72 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed p-8 text-center transition-colors ${dragActive ? "border-primary bg-primary/5" : file ? "border-primary/40 bg-primary/[0.03]" : "border-border bg-surface hover:border-border"}`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
            >
              <input className="sr-only" type="file" accept="application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" disabled={loading || connecting} onChange={(event) => chooseFile(event.target.files?.[0] || null)} />
              <span className="icon-tile h-12 w-12">{file ? <FileText size={22} /> : <FileUp size={22} />}</span>
              <h2 className="font-display mt-5 max-w-full break-words text-lg font-normal text-foreground">{file ? file.name : "Choose a resume"}</h2>
              <p className="mt-2 text-sm text-muted-foreground">{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB, ready to parse` : "Drop the file here or open your file browser"}</p>
            </label>
            <label className="mt-4 flex items-start gap-3 text-sm leading-6"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={enrichSkills} disabled={loading || connecting} onChange={(event) => setEnrichSkills(event.target.checked)} /><span>Optional AI skill enrichment · 1 analysis unit<span className="mt-1 block text-xs leading-5 text-muted-foreground">Default parsing extracts text and catalog skills without generative AI. Enable this to send resume text for additional source-supported AI skill suggestions. One unit is charged only if useful additional skills are found; current Premium analysis policy applies. Review the results before using them.</span></span></label>
            <Button type="submit" className="mt-4 w-full" disabled={!file || loading || connecting}>
              {loading ? "Extracting evidence..." : "Parse resume"} <ArrowRight size={16} />
            </Button>
            {error ? <div className="mt-4 flex gap-3 border border-coral/30 bg-coral/5 p-4 text-sm text-coral" role="alert"><AlertCircle size={18} className="shrink-0" /> {error}</div> : null}
          </form>

          <aside className="border-t border-border pt-7 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-2">
            <p className="data-label">After parsing</p>
            <ol className="mt-5 grid gap-5 text-sm text-muted-foreground">
              <li className="flex gap-3"><span className="font-semibold text-primary">01</span><span>Review extracted skills and experience.</span></li>
              <li className="flex gap-3"><span className="font-semibold text-primary">02</span><span>Add a target role to preserve its job snapshot.</span></li>
              <li className="flex gap-3"><span className="font-semibold text-primary">03</span><span>Approve evidence, then review tailored changes while keeping your source layout.</span></li>
            </ol>
          </aside>
        </div>

        {data ? (
          <section className="mt-10 border-t border-border pt-8" aria-live="polite">
            <div className="grid gap-7 lg:grid-cols-[1fr_auto] lg:items-start">
              <div>
                <div className="flex items-center gap-2 text-sm font-bold text-primary"><CheckCircle2 size={17} /> Resume parsed</div>
                <h2 className="font-display mt-3 text-2xl font-normal text-foreground">Review the extracted signals</h2>
                {data.extraction_mode ? <p className="mt-2 text-xs font-semibold text-primary">{data.extraction_mode === "enriched" ? `AI-enriched skills · ${data.enrichment_units ?? 0} analysis units charged` : "Deterministic parsing · no generative AI"}</p> : null}
                {data.warnings?.map((warning) => <p key={warning} className="mt-2 text-sm leading-6 text-muted-foreground" role="status">{warning}</p>)}
                <p className="mt-2 text-sm text-muted-foreground">Estimated experience: {data.experience_years} years. These values remain editable source material, not verified claims.</p>
                {data.source_available && data.source_format ? <p className="mt-3 text-sm font-semibold text-primary">Original {data.source_format.toUpperCase()} retained for preview and tailoring.</p> : <p className="mt-3 text-sm text-coral">The original file is unavailable. Upload your source again before tailoring.</p>}
                <div className="mt-5 flex flex-wrap gap-2">
                  {data.skills.slice(0, 30).map((skill) => <span key={skill} className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs font-semibold text-foreground">{skill}</span>)}
                  {!data.skills.length ? <span className="text-sm text-muted-foreground">No skills were confidently extracted.</span> : null}
                </div>
              </div>
              <div className="grid min-w-60 gap-2">
                {opportunityId ? <Button onClick={() => void connectUploadedResume()} disabled={connecting || !data.source_available || !data.source_format}><Target size={16} /> {connecting ? "Connecting resume..." : "Use for this opportunity"}</Button> : <Button asChild><Link href="/workspace?new=1"><Target size={16} /> Add target role</Link></Button>}
                <Button asChild variant="secondary"><Link href={`/resume/preview?resume=${data.resume_id}`}><FileText size={16} /> View original resume</Link></Button>
                {connectionError ? <p className="text-sm leading-6 text-coral" role="alert">{connectionError} Your upload is saved. Retry connecting it.</p> : null}
              </div>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}

export default function ResumePage() {
  return <Suspense fallback={<main className="app-page"><div className="page-container"><LoadingBlock rows={5} /></div></main>}><ResumeUploadContent /></Suspense>;
}
