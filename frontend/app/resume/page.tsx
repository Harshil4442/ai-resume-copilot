"use client";

import { useState } from "react";
import type { ResumeParseResponse } from "../../lib/types";
import { apiPostForm } from "../../lib/api";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { FileUp, Target, Search, Clock, FileText, CheckCircle2, ChevronRight, AlertCircle, ArrowRight } from "lucide-react";
import Link from "next/link";

export default function ResumePage() {
  const [file, setFile] = useState<File | null>(null);
  const [data, setData] = useState<ResumeParseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragActive, setDragActive] = useState(false);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

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
      window.dispatchEvent(new Event("refresh_analysis_units"));
    } catch (err: any) {
      setError(err.message || "Failed to parse resume.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader 
        badge="Resume Intelligence"
        title="Turn a resume into structured career data."
        subtitle="Extract sections, skills, experience, and contact signals before matching against jobs and market demand."
      />

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_400px] gap-8">
        <GlassCard className="p-0 overflow-hidden" hoverEffect={false}>
          <div className="p-8 md:p-12 h-full flex flex-col justify-center">
            <h2 className="text-3xl font-black text-white tracking-tighter mb-4">Upload Document</h2>
            <p className="text-slate-300 font-medium mb-8">
              PDF and DOCX formats are supported. Our LLM-powered parser will securely extract your core data for analysis.
            </p>

            <form onSubmit={onSubmit} className="space-y-6">
              <div 
                className={`relative border-2 border-dashed rounded-3xl p-10 text-center transition-all ${
                  dragActive 
                    ? "border-primary bg-primary/5 scale-[1.02]" 
                    : file 
                      ? "border-emerald-300 bg-emerald-900/30/50" 
                      : "border-slate-600 hover:border-primary/50 hover:bg-slate-900/50"
                }`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
              >
                <input
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
                  type="file"
                  accept="application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                />
                
                <div className="flex flex-col items-center justify-center space-y-4 pointer-events-none">
                  <div className={`w-16 h-16 rounded-2xl flex items-center justify-center ${file ? 'bg-emerald-100 text-emerald-400' : 'bg-blue-100 text-blue-600'}`}>
                    {file ? <FileText size={32} /> : <FileUp size={32} />}
                  </div>
                  <div>
                    <h3 className="text-xl font-black text-white">
                      {file ? file.name : "Drag & drop or click to browse"}
                    </h3>
                    <p className="text-sm text-slate-400 font-medium mt-2">
                      {file ? `${(file.size / 1024 / 1024).toFixed(2)} MB • Ready to parse` : "Supports PDF, DOCX (Max 5MB)"}
                    </p>
                  </div>
                </div>
              </div>

              <AnimatedButton type="submit" disabled={!file || loading} className="w-full py-4 text-lg shadow-lg" showArrow>
                {loading ? "Extracting Data..." : "Upload & Parse"}
              </AnimatedButton>

              {error && (
                <FadeIn>
                  <div className="flex items-center gap-2 text-sm text-rose-400 bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3 shadow-sm">
                    <AlertCircle size={16} /> {error}
                  </div>
                </FadeIn>
              )}
            </form>
          </div>
        </GlassCard>

        <StaggerContainer className="space-y-4 h-full flex flex-col justify-center">
          {[
            { icon: Search, title: "Precision Parsing", desc: "Extracts hard skills, soft skills, and core metrics automatically." },
            { icon: Target, title: "Job Matching", desc: "Compare parsed data against live job descriptions." },
            { icon: Clock, title: "History Tracking", desc: "Keep multiple versions of resumes in your workspace." }
          ].map((item, i) => (
            <StaggerItem key={i}>
              <GlassCard className="p-5 flex items-start gap-4">
                <div className="w-10 h-10 rounded-xl bg-slate-800 border border-slate-700 flex items-center justify-center flex-shrink-0 text-slate-200">
                  <item.icon size={20} />
                </div>
                <div>
                  <h3 className="text-sm font-black text-white tracking-tight">{item.title}</h3>
                  <p className="text-xs text-slate-400 font-medium mt-1 leading-relaxed">{item.desc}</p>
                </div>
              </GlassCard>
            </StaggerItem>
          ))}
        </StaggerContainer>
      </div>

      {loading && (
        <FadeIn>
          <GlassCard className="p-16 text-center border-dashed border-slate-600 border-2" hoverEffect={false}>
            <div className="mx-auto w-12 h-12 rounded-full border-4 border-slate-700 border-t-primary animate-spin mb-6"></div>
            <h2 className="text-xl font-bold text-white tracking-tight">AI is parsing your document...</h2>
            <p className="text-sm text-slate-400 mt-2">Extracting your career timeline, education, and mapping technical skills.</p>
          </GlassCard>
        </FadeIn>
      )}

      {data && (
        <FadeIn>
          <GlassCard className="p-8 border-emerald-800 bg-emerald-900/30/30" hoverEffect={false}>
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 mb-8 border-b border-emerald-800 pb-8">
              <div>
                <div className="text-[10px] font-black text-emerald-400 uppercase tracking-widest flex items-center gap-2 mb-2">
                  <CheckCircle2 size={14} /> Extraction Complete
                </div>
                <h2 className="text-3xl font-black text-white tracking-tighter">Resume #{data.resume_id}</h2>
              </div>
              <div className="bg-slate-950 px-6 py-4 rounded-2xl shadow-sm border border-emerald-800 text-center">
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Estimated Experience</div>
                <div className="text-3xl font-black text-primary">{data.experience_years} <span className="text-sm font-bold text-slate-400">YRS</span></div>
              </div>
            </div>

            <div className="mb-8">
              <h3 className="text-sm font-black text-white uppercase tracking-wider mb-4">Extracted Signals</h3>
              <div className="flex flex-wrap gap-2">
                {data.skills.map((s) => (
                  <span key={s} className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-bold text-slate-200 shadow-sm hover:scale-105 transition-transform cursor-default">
                    {s}
                  </span>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <Link href="/resume/preview" className="flex items-center justify-between bg-slate-950 border border-slate-700 rounded-xl p-4 font-bold text-sm text-slate-200 hover:border-primary/50 hover:shadow-md transition-all group">
                Preview Data
                <ChevronRight size={16} className="text-slate-400 group-hover:text-primary transition-colors" />
              </Link>
              <Link href="/jobs" className="flex items-center justify-between bg-slate-950 border border-slate-700 rounded-xl p-4 font-bold text-sm text-slate-200 hover:border-primary/50 hover:shadow-md transition-all group">
                Run Job Match
                <Target size={16} className="text-slate-400 group-hover:text-primary transition-colors" />
              </Link>
              <Link href="/market" className="flex items-center justify-between bg-slate-950 border border-slate-700 rounded-xl p-4 font-bold text-sm text-slate-200 hover:border-primary/50 hover:shadow-md transition-all group">
                Market Demand
                <Search size={16} className="text-slate-400 group-hover:text-primary transition-colors" />
              </Link>
              <Link href="/dashboard" className="flex items-center justify-between bg-slate-900 border border-slate-800 rounded-xl p-4 font-bold text-sm text-white hover:bg-slate-800 hover:shadow-md transition-all group">
                Dashboard
                <ArrowRight size={16} className="text-slate-400 group-hover:text-white transition-colors" />
              </Link>
            </div>
          </GlassCard>
        </FadeIn>
      )}
    </main>
  );
}
