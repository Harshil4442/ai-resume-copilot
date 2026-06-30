"use client";

import { useState } from "react";
import type { ResumeParseResponse } from "../../lib/types";
import { apiPostForm } from "../../lib/api";

export default function ResumePage() {
  const [file, setFile] = useState<File | null>(null);
  const [data, setData] = useState<ResumeParseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setData(null);
    if (!file) return;
    setLoading(true);

    const form = new FormData();
    form.append("file", file);

    try {
      const json = await apiPostForm<ResumeParseResponse>("/resume/parse", form);
      setData(json);
    } catch (err: any) {
      setError(err.message || "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell space-y-8">
      <section className="grid grid-cols-1 lg:grid-cols-[0.9fr_1.1fr] gap-6">
        <div className="product-hero text-left p-7 md:p-9">
          <div className="label-kicker flex items-center gap-3"><span className="pulse-dot" />Resume Intelligence</div>
          <h1 className="text-5xl md:text-6xl font-black leading-[0.88] mt-4 text-slate-950">Turn a resume into structured career data.</h1>
          <p className="text-slate-600 mt-4 leading-relaxed">
            Extract sections, skills, experience, and contact signals before matching against jobs and market demand.
          </p>
          <div className="grid grid-cols-3 gap-3 mt-8">
            {["Skills", "Sections", "Signals"].map((item) => (
              <div key={item} className="premium-card p-3 tilt-lift">
                <div className="text-sm font-black text-slate-950">{item}</div>
                <div className="text-[11px] text-slate-500 mt-1">parsed</div>
              </div>
            ))}
          </div>
        </div>

        <form onSubmit={onSubmit} className="panel kinetic-border p-6 md:p-8 flex flex-col justify-center">
          <div className="label-kicker">Upload</div>
          <h2 className="text-4xl font-black text-slate-950 mt-2 ink-gradient">Drop in your latest resume.</h2>
          <p className="text-sm text-slate-500 mt-2">PDF and DOCX are supported by the backend parser.</p>

          <label className="mt-8 block rounded-lg border-2 border-dashed border-blue-200 bg-blue-50/60 p-7 text-center hover:border-blue-400 hover:bg-white transition cursor-pointer tilt-lift">
            <input
              className="hidden"
              type="file"
              accept="application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <span className="block text-lg font-black text-slate-950">{file ? file.name : "Choose resume file"}</span>
            <span className="block text-xs text-slate-500 mt-1">The parsed result is saved to your profile workspace.</span>
          </label>

          <button disabled={!file || loading} className="btn-primary mt-5">
            {loading ? "Parsing resume..." : "Upload and Parse"}
          </button>

          {error && <div className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}
        </form>
      </section>

      {data && (
        <section className="panel kinetic-border p-6 space-y-5">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <div className="label-kicker">Parsed Resume</div>
              <h2 className="text-2xl font-black text-slate-950 mt-1">Resume #{data.resume_id}</h2>
            </div>
            <div className="metric-card kinetic-border">
              <div className="text-xs text-slate-500">Estimated experience</div>
              <div className="text-3xl font-black ink-gradient">{data.experience_years} yrs</div>
            </div>
          </div>
          <div>
            <div className="text-sm font-black text-slate-900 mb-3">Extracted Skills</div>
            <div className="flex flex-wrap gap-2">
              {data.skills.map((s) => <span key={s} className="signal-chip">{s}</span>)}
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <a className="btn-secondary" href="/resume/preview">Preview Document</a>
            <a className="btn-secondary" href="/jobs">Run job match</a>
            <a className="btn-secondary" href="/market">Compare market</a>
            <a className="btn-secondary" href="/dashboard">View dashboard</a>
          </div>
        </section>
      )}
    </main>
  );
}
