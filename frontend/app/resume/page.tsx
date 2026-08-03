"use client";

import { AlertCircle, ArrowRight, CheckCircle2, FileText, FileUp, ShieldCheck, Target } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "../../components/ui/Button";
import { trackEvent } from "../../lib/analytics";
import { apiPostForm } from "../../lib/api";
import type { ResumeParseResponse } from "../../lib/types";

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ACCEPTED_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

export default function ResumePage() {
  const [file, setFile] = useState<File | null>(null);
  const [data, setData] = useState<ResumeParseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragActive, setDragActive] = useState(false);

  function chooseFile(candidate: File | null) {
    setData(null);
    setError(null);
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
    setData(null);
    setLoading(true);
    trackEvent("resume_upload_started", { file_type: file.type, size_bytes: file.size });
    const form = new FormData();
    form.append("file", file);
    try {
      const parsed = await apiPostForm<ResumeParseResponse>("/resume/parse", form);
      setData(parsed);
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

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="grid gap-6 border-b border-white/10 pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="eyebrow">Resume evidence</p>
            <h1 className="mt-2 text-3xl font-black text-neutral-100 sm:text-4xl">Add your source resume</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-500">HireWiz extracts a private working copy. You choose which facts become approved evidence.</p>
          </div>
          <div className="flex gap-5 text-xs text-neutral-500">
            <span className="flex items-center gap-2"><ShieldCheck size={16} className="text-primary" /> PDF or DOCX</span>
            <span>5 MB maximum</span>
          </div>
        </header>

        <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_340px]">
          <form onSubmit={onSubmit}>
            <label
              className={`relative flex min-h-72 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed p-8 text-center transition-colors ${dragActive ? "border-primary bg-primary/5" : file ? "border-primary/40 bg-primary/[0.03]" : "border-white/20 bg-white/[0.015] hover:border-white/35"}`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
            >
              <input className="sr-only" type="file" accept="application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => chooseFile(event.target.files?.[0] || null)} />
              <span className="icon-tile h-12 w-12">{file ? <FileText size={22} /> : <FileUp size={22} />}</span>
              <h2 className="mt-5 max-w-full break-words text-lg font-black text-neutral-200">{file ? file.name : "Choose a resume"}</h2>
              <p className="mt-2 text-sm text-neutral-500">{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB, ready to parse` : "Drop the file here or open your file browser"}</p>
            </label>
            <Button type="submit" className="mt-4 w-full" disabled={!file || loading}>
              {loading ? "Extracting evidence..." : "Parse resume"} <ArrowRight size={16} />
            </Button>
            {error ? <div className="mt-4 flex gap-3 border border-coral/30 bg-coral/5 p-4 text-sm text-[#ffab9e]" role="alert"><AlertCircle size={18} className="shrink-0" /> {error}</div> : null}
          </form>

          <aside className="border-t border-white/10 pt-7 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-2">
            <p className="data-label">After parsing</p>
            <ol className="mt-5 grid gap-5 text-sm text-neutral-400">
              <li className="flex gap-3"><span className="font-black text-primary">01</span><span>Review extracted skills and experience.</span></li>
              <li className="flex gap-3"><span className="font-black text-primary">02</span><span>Add a target role to preserve its job snapshot.</span></li>
              <li className="flex gap-3"><span className="font-black text-primary">03</span><span>Approve evidence before tailoring or interview preparation.</span></li>
            </ol>
          </aside>
        </div>

        {data ? (
          <section className="mt-10 border-t border-white/10 pt-8" aria-live="polite">
            <div className="grid gap-7 lg:grid-cols-[1fr_auto] lg:items-start">
              <div>
                <div className="flex items-center gap-2 text-sm font-bold text-[#69debd]"><CheckCircle2 size={17} /> Resume parsed</div>
                <h2 className="mt-3 text-2xl font-black text-neutral-100">Review the extracted signals</h2>
                <p className="mt-2 text-sm text-neutral-500">Estimated experience: {data.experience_years} years. These values remain editable source material, not verified claims.</p>
                <div className="mt-5 flex flex-wrap gap-2">
                  {data.skills.slice(0, 30).map((skill) => <span key={skill} className="rounded-md border border-white/10 bg-white/[0.025] px-2.5 py-1.5 text-xs font-semibold text-neutral-300">{skill}</span>)}
                  {!data.skills.length ? <span className="text-sm text-neutral-600">No skills were confidently extracted.</span> : null}
                </div>
              </div>
              <div className="grid min-w-60 gap-2">
                <Button asChild><Link href="/workspace?new=1"><Target size={16} /> Add target role</Link></Button>
                <Button asChild variant="secondary"><Link href="/resume/preview"><FileText size={16} /> Inspect parsed data</Link></Button>
              </div>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}
