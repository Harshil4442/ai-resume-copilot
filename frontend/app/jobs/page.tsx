"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPostJson } from "../../lib/api";
import TailoredResumeViewer from "../../components/TailoredResumeViewer";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import ScoreRing from "../../components/ui/ScoreRing";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import ScrollReveal from "../../components/ui/ScrollReveal";
import { Target, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Sparkles, User, FileText, ChevronRight } from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

type ResumeItem = {
  id: number;
  filename: string;
  created_at: string;
};

type PartialMatch = {
  skill: string;
  coverage: number;
  via: string;
};

type DimensionScore = {
  name: string;
  score: number;
  feedback: string;
};

type MatchResponse = {
  match_id: number;
  match_score: number;
  grade: string;
  required_skills: string[];
  full_matches: string[];
  partial_matches: PartialMatch[];
  true_gaps: string[];
  skill_verification_rate: number;
  dimensions: DimensionScore[];
  fit_summary: string;
  improvement_tips: string[];
};

type HistoryItem = {
  match_id: number;
  job_title: string;
  company: string;
  match_score: number;
  created_at: string;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  confidence?: "high" | "medium" | "low" | string;
  suggested_followups?: string[];
};

type RagResponse = {
  answer: string;
  confidence: "high" | "medium" | "low";
  suggested_followups: string[];
};

const starterQuestions = [
  "Why is my match score low?",
  "Which skills am I missing?",
  "Which skills are actually proven in my resume?",
  "Which project should I highlight for this job?",
  "How should I prepare for this interview?",
  "What should I improve first?",
];

function confidenceClass(value: string) {
  const v = value.toLowerCase();
  if (v === "high") return "bg-emerald-900/30 text-emerald-400 border-emerald-800";
  if (v === "low") return "bg-rose-900/30 text-rose-400 border-rose-800";
  return "bg-amber-900/30 text-amber-400 border-amber-800";
}

function gradeColor(g: string) {
  if (g.startsWith("A")) return "bg-emerald-900/30 text-emerald-400 border-emerald-800";
  if (g.startsWith("B")) return "bg-blue-900/30 text-blue-400 border-blue-700";
  if (g.startsWith("C")) return "bg-amber-900/30 text-amber-400 border-amber-800";
  return "bg-rose-900/30 text-rose-400 border-rose-800";
}

function SkillTag({ label, variant }: { label: string; variant: "match" | "gap" | "partial" | "neutral" }) {
  const cls = {
    match: "bg-emerald-900/30 text-emerald-400 border-emerald-800 shadow-sm",
    gap: "bg-rose-900/30 text-rose-400 border-rose-800 shadow-sm",
    partial: "bg-amber-900/30 text-amber-400 border-amber-800 shadow-sm",
    neutral: "bg-slate-900/50 text-slate-200 border-slate-700 shadow-sm",
  }[variant];
  return <span className={twMerge(clsx("px-3 py-1 rounded-full text-xs font-bold border transition-transform hover:-translate-y-0.5", cls))}>{label}</span>;
}

function DimBar({ dim }: { dim: DimensionScore }) {
  const barColor = dim.score >= 75 ? "bg-emerald-900/300" : dim.score >= 50 ? "bg-amber-400" : "bg-rose-400";
  return (
    <GlassCard className="p-4" hoverEffect={false}>
      <div className="flex justify-between items-center gap-3 mb-2">
        <span className="text-xs font-bold text-slate-200 uppercase tracking-wider">{dim.name}</span>
        <span className="text-sm font-black text-white">{dim.score.toFixed(0)}/100</span>
      </div>
      <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden mb-3">
        <div className={twMerge(clsx("h-2 rounded-full", barColor))} style={{ width: `${Math.max(3, Math.min(100, dim.score))}%` }} />
      </div>
      <p className="text-xs text-slate-400 leading-relaxed font-medium">{dim.feedback}</p>
    </GlassCard>
  );
}

function EmptyState({ title, body, href }: { title: string; body: string; href?: string }) {
  return (
    <div className="border border-amber-800 rounded-xl px-5 py-4 text-sm bg-amber-900/30/80 backdrop-blur-sm flex items-start gap-3">
      <AlertTriangle className="text-amber-500 mt-0.5" size={18} />
      <div>
        <p className="text-amber-900 font-bold">{title}</p>
        <p className="text-amber-400 text-xs mt-1 leading-relaxed">{body}</p>
        {href && (
          <a href={href} className="text-amber-800 font-bold flex items-center gap-1 hover:underline text-xs mt-3">
            Open resume page <ChevronRight size={14} />
          </a>
        )}
      </div>
    </div>
  );
}

function AskAiPanel({
  match,
  resumeId,
}: {
  match: MatchResponse;
  resumeId: string;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(question: string) {
    const clean = question.trim();
    if (!clean || loading) return;
    setError(null);
    setLoading(true);
    const userMessage: ChatMessage = { role: "user", content: clean };
    const recent = [...messages, userMessage].slice(-6).map((msg) => ({
      role: msg.role,
      content: msg.content,
    }));
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    try {
      const res = await apiPostJson<RagResponse>("/rag/ask", {
        resume_id: Number(resumeId),
        job_match_id: match.match_id,
        question: clean,
        recent_messages: recent,
      });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.answer,
          confidence: res.confidence,
          suggested_followups: res.suggested_followups,
        },
      ]);
      window.dispatchEvent(new Event("refresh_credits"));
    } catch (e: any) {
      setError(e?.message || "Ask AI failed");
    } finally {
      setLoading(false);
    }
  }

  const latestAssistant = [...messages].reverse().find((msg) => msg.role === "assistant");

  return (
    <GlassCard className="p-0 overflow-hidden flex flex-col" hoverEffect={false}>
      <div className="bg-slate-900 px-6 py-5 text-white">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="text-[10px] font-black uppercase tracking-[0.2em] text-blue-400 flex items-center gap-2 mb-1.5">
              <Sparkles size={12} /> Grounded Copilot
            </div>
            <h2 className="text-2xl font-black tracking-tighter text-white">Ask AI about this match</h2>
            <p className="text-sm text-slate-400 mt-1">Answers are grounded in this resume, JD, score breakdown, and match analysis.</p>
          </div>
          {latestAssistant?.confidence && (
            <span className={twMerge(clsx("px-3 py-1 rounded-full border text-xs font-bold shadow-sm", confidenceClass(latestAssistant.confidence)))}>
              {latestAssistant.confidence} confidence
            </span>
          )}
        </div>
      </div>

      <div className="p-6 space-y-5 bg-slate-900/50 flex-grow">
        {messages.length === 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {starterQuestions.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                className="text-left rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm font-semibold text-slate-200 hover:border-primary/30 hover:bg-primary/5 hover:text-primary transition-all shadow-sm"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {messages.length > 0 && (
          <div className="max-h-[460px] overflow-y-auto space-y-4 pr-2 custom-scrollbar">
            {messages.map((msg, idx) => (
              <div
                key={`${msg.role}-${idx}-${msg.content.slice(0, 24)}`}
                className={twMerge(clsx("flex", msg.role === "user" ? "justify-end" : "justify-start"))}
              >
                <div className={twMerge(clsx(
                  "max-w-[85%] rounded-2xl px-5 py-3.5 text-sm leading-relaxed shadow-sm",
                  msg.role === "user" ? "bg-primary text-white" : "bg-slate-950 text-slate-100 border border-slate-700",
                ))}>
                  <div className="whitespace-pre-wrap">{msg.content}</div>
                  {msg.role === "assistant" && msg.confidence && (
                    <div className="mt-3">
                      <span className={twMerge(clsx("px-2.5 py-0.5 rounded-full border text-[10px] font-bold", confidenceClass(msg.confidence)))}>
                        {msg.confidence} confidence
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="rounded-2xl px-5 py-3.5 text-sm bg-slate-950 text-slate-400 border border-slate-700 flex items-center gap-2 shadow-sm">
                  <div className="w-4 h-4 rounded-full border-2 border-slate-600 border-t-primary animate-spin" />
                  Thinking through the match context...
                </div>
              </div>
            )}
          </div>
        )}

        {latestAssistant?.suggested_followups && latestAssistant.suggested_followups.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-2">
            {latestAssistant.suggested_followups.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                className="rounded-full border border-blue-700 bg-blue-900/30 px-3 py-1.5 text-xs font-bold text-blue-400 hover:bg-blue-100 transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
          className="flex gap-3 pt-2"
        >
          <input
            className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 shadow-sm"
            placeholder="Ask about score, gaps, evidence, interview prep..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <AnimatedButton
            type="submit"
            disabled={!input.trim() || loading}
          >
            Ask
          </AnimatedButton>
        </form>
        {error && <div className="text-sm text-rose-400 bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3 shadow-sm">{error}</div>}
      </div>
    </GlassCard>
  );
}

export default function JobsPage() {
  const [resumes, setResumes] = useState<ResumeItem[]>([]);
  const [resumesLoading, setResumesLoading] = useState(true);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [selectedResumeId, setSelectedResumeId] = useState("");

  const [jobTitle, setJobTitle] = useState("");
  const [company, setCompany] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [data, setData] = useState<MatchResponse | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  // Tailoring states
  const [tailorModalOpen, setTailorModalOpen] = useState(false);
  const [templateType, setTemplateType] = useState("ats");
  const [tailoring, setTailoring] = useState(false);
  const [tailoredResume, setTailoredResume] = useState<string | null>(null);
  const [tailoredPdfBase64, setTailoredPdfBase64] = useState<string | null>(null);
  const [tailorError, setTailorError] = useState<string | null>(null);

  async function handleTailor() {
    if (!data) return;
    setTailoring(true);
    setTailorError(null);
    try {
      const res = await apiPostJson<{ tailored_resume_markdown: string, pdf_base64?: string }>(`/jobs/match/${data.match_id}/tailor`, {
        template_type: templateType
      });
      setTailoredResume(res.tailored_resume_markdown);
      setTailoredPdfBase64(res.pdf_base64 || null);
      setTailorModalOpen(false);
      window.dispatchEvent(new Event("refresh_credits"));
    } catch (err: any) {
      setTailorError(err?.message || "Failed to tailor resume.");
    } finally {
      setTailoring(false);
    }
  }

  useEffect(() => {
    apiGet<{ resumes: ResumeItem[] }>("/resume/list")
      .then((res) => {
        setResumes(res.resumes);
        if (res.resumes.length > 0) setSelectedResumeId(String(res.resumes[0].id));
      })
      .catch((err: any) => {
        setResumeError(err?.message || "Failed to load resumes. Are you logged in?");
      })
      .finally(() => setResumesLoading(false));
  }, []);

  useEffect(() => {
    apiGet<{ matches: HistoryItem[] }>("/jobs/matches")
      .then((res) => setHistory(res.matches))
      .catch(() => {});
  }, [data]);

  const canSubmit = useMemo(
    () => Boolean(selectedResumeId && jobTitle.trim() && jobDescription.trim()),
    [selectedResumeId, jobTitle, jobDescription],
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const result = await apiPostJson<MatchResponse>("/jobs/match", {
        resume_id: Number(selectedResumeId),
        job_title: jobTitle.trim(),
        company: company.trim() || undefined,
        job_description: jobDescription.trim(),
      });
      setData(result);
      window.dispatchEvent(new Event("refresh_credits"));
    } catch (err: any) {
      setError(err?.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader 
        badge="Job Intelligence"
        title="Match a resume, then interrogate the result."
        subtitle="Run a multidimensional match analysis and ask follow-up questions grounded in the resume, job description, gaps, and recruiter scoring."
      />

      <div className="grid grid-cols-1 lg:grid-cols-[400px_1fr] xl:grid-cols-[440px_1fr] gap-8 items-start">
        <ScrollReveal direction="left">
          <GlassCard className="lg:sticky lg:top-24 p-6 md:p-8 flex flex-col space-y-6">
          <div>
            <h2 className="text-2xl font-black text-white tracking-tighter">Analyze a target job</h2>
            <p className="text-sm text-slate-400 mt-1 font-medium">Choose a parsed resume and paste the JD.</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5 flex-grow">
            <div>
              <label className="block text-sm font-bold text-slate-200 mb-2">Resume</label>
              {resumesLoading ? (
                <div className="w-full border rounded-xl px-4 py-3 text-sm text-slate-400 bg-slate-900/50 animate-pulse">Loading resumes...</div>
              ) : resumeError ? (
                <div className="w-full border border-rose-800 rounded-xl px-4 py-3 text-sm bg-rose-900/30 shadow-sm">
                  <p className="text-rose-400 font-bold flex items-center gap-1.5"><AlertTriangle size={14} /> Failed to load resumes</p>
                  <p className="text-rose-400 text-xs mt-1">{resumeError}</p>
                </div>
              ) : resumes.length === 0 ? (
                <EmptyState title="No parsed resumes found" body="Upload and parse a resume first to run a match." href="/resume" />
              ) : (
                <select
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 shadow-sm"
                  value={selectedResumeId}
                  onChange={(e) => setSelectedResumeId(e.target.value)}
                >
                  {resumes.map((r) => (
                    <option key={r.id} value={String(r.id)}>
                      {r.filename || `Resume #${r.id}`} | ID: {r.id} | {new Date(r.created_at).toLocaleDateString()}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div className="grid grid-cols-1 gap-5">
              <div>
                <label className="block text-sm font-bold text-slate-200 mb-2">Job Title</label>
                <input
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 shadow-sm"
                  placeholder="Senior Backend Engineer"
                  value={jobTitle}
                  onChange={(e) => setJobTitle(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm font-bold text-slate-200 mb-2">Company <span className="text-slate-400 font-normal">(optional)</span></label>
                <input
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 shadow-sm"
                  placeholder="Acme Corp"
                  value={company}
                  onChange={(e) => setCompany(e.target.value)}
                />
              </div>
            </div>

            <div className="flex-grow">
              <label className="block text-sm font-bold text-slate-200 mb-2">Job Description</label>
              <textarea
                className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 shadow-sm min-h-[220px] resize-y"
                placeholder="Paste the full job description here..."
                value={jobDescription}
                onChange={(e) => setJobDescription(e.target.value)}
              />
            </div>

            <AnimatedButton
              type="submit"
              disabled={!canSubmit || loading}
              className="w-full py-4 text-base mt-2"
              showArrow
            >
              {loading ? "Analyzing match..." : "Analyze Match"}
            </AnimatedButton>

            {error && <div className="text-sm text-rose-400 bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3 shadow-sm">{error}</div>}
          </form>
        </GlassCard>
        </ScrollReveal>

        <ScrollReveal direction="right">
        <div className="space-y-6">
          {!data && !loading && (
            <FadeIn delay={0.2}>
              <GlassCard className="p-12 text-center border-dashed border-slate-600 border-2" hoverEffect={false}>
                <div className="mx-auto w-16 h-16 rounded-2xl bg-slate-900 text-white flex items-center justify-center font-black text-2xl shadow-lg mb-6">AI</div>
                <h2 className="text-2xl font-black text-white tracking-tighter">Your match analysis will appear here</h2>
                <p className="text-slate-400 mt-3 max-w-md mx-auto leading-relaxed">
                  After the score is generated, you can ask AI questions about gaps, proof, score reasoning, and interview prep.
                </p>
              </GlassCard>
            </FadeIn>
          )}

          {loading && (
            <FadeIn>
              <GlassCard className="p-12 text-center" hoverEffect={false}>
                <div className="mx-auto w-12 h-12 rounded-full border-4 border-slate-700 border-t-primary animate-spin mb-6"></div>
                <h2 className="text-xl font-bold text-white tracking-tight">Analyzing resume against job description...</h2>
                <p className="text-sm text-slate-400 mt-2">Extracting requirements, computing verification rates, and scoring dimensions.</p>
              </GlassCard>
            </FadeIn>
          )}

          {data && (
            <StaggerContainer className="space-y-6">
              <StaggerItem>
                <GlassCard className="p-6 md:p-8" hoverEffect={false}>
                  <div className="flex items-start gap-6 flex-wrap">
                    <ScoreRing score={data.match_score} size={110} strokeWidth={8} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 flex-wrap mb-2">
                        <h2 className="text-2xl font-black text-white tracking-tighter">
                          {jobTitle}{company ? ` @ ${company}` : ""}
                        </h2>
                        <span className={twMerge(clsx("text-xs font-bold px-3 py-1 rounded-full border shadow-sm", gradeColor(data.grade)))}>Grade {data.grade}</span>
                      </div>
                      <div className="text-xs font-semibold text-slate-400 mb-5 flex items-center gap-2">
                        <Target size={12} /> Match #{data.match_id}
                      </div>
                      
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Required skills</div>
                          <div className="text-2xl font-black text-white">{data.required_skills.length}</div>
                        </div>
                        <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">True gaps</div>
                          <div className="text-2xl font-black text-white">{data.true_gaps.length}</div>
                        </div>
                        <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Skill verification</div>
                          <div className="text-2xl font-black text-white">{data.skill_verification_rate}%</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </GlassCard>
              </StaggerItem>

              {data.fit_summary && (
                <StaggerItem>
                  <div className="bg-blue-900/30/80 backdrop-blur-sm border border-blue-700 rounded-2xl p-5 shadow-sm">
                    <div className="text-xs font-black text-blue-600 uppercase tracking-wider mb-2 flex items-center gap-2">
                      <Sparkles size={14} /> AI Fit Analysis
                    </div>
                    <p className="text-sm text-slate-200 leading-relaxed font-medium">{data.fit_summary}</p>
                  </div>
                </StaggerItem>
              )}

              <StaggerItem className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <GlassCard className="p-6 md:p-8" hoverEffect={false}>
                  <div className="text-[10px] font-black uppercase tracking-wider text-slate-400 mb-6 flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-emerald-500" /> Skill Coverage
                  </div>
                  <div className="space-y-6">
                    <div>
                      <div className="text-xs font-bold text-slate-100 mb-3 flex justify-between">
                        <span>Full Matches</span>
                        <span className="text-slate-400 bg-slate-800 px-2 py-0.5 rounded-full">{data.full_matches.length}</span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {data.full_matches.length ? data.full_matches.map((s) => <SkillTag key={s} label={s} variant="match" />) : <SkillTag label="No full matches yet" variant="neutral" />}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-bold text-slate-100 mb-3 flex justify-between">
                        <span>Partial Coverage</span>
                        <span className="text-slate-400 bg-slate-800 px-2 py-0.5 rounded-full">{data.partial_matches.length}</span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {data.partial_matches.length ? data.partial_matches.map((p) => (
                          <SkillTag key={p.skill} label={`${p.skill} (${p.coverage}% via ${p.via})`} variant="partial" />
                        )) : <SkillTag label="No partial matches" variant="neutral" />}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-bold text-slate-100 mb-3 flex justify-between">
                        <span>True Gaps</span>
                        <span className="text-slate-400 bg-slate-800 px-2 py-0.5 rounded-full">{data.true_gaps.length}</span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {data.true_gaps.length ? data.true_gaps.map((s) => <SkillTag key={s} label={s} variant="gap" />) : <SkillTag label="No true gaps found" variant="match" />}
                      </div>
                    </div>
                  </div>
                </GlassCard>

                <GlassCard className="p-6 md:p-8 bg-slate-900/50" hoverEffect={false}>
                  <div className="text-[10px] font-black uppercase tracking-wider text-slate-400 mb-6 flex items-center gap-2">
                    <User size={14} className="text-primary" /> Recruiter Dimensions
                  </div>
                  <div className="space-y-4">
                    {data.dimensions.map((dim) => <DimBar key={dim.name} dim={dim} />)}
                  </div>
                </GlassCard>
              </StaggerItem>

              {data.improvement_tips.length > 0 && (
                <StaggerItem>
                  <GlassCard className="p-6 md:p-8 border-amber-800 bg-amber-900/30/50" hoverEffect={false}>
                    <div className="text-[10px] font-black text-amber-600 uppercase tracking-wider mb-4 flex items-center gap-2">
                      <AlertTriangle size={14} /> How to Improve Your Match
                    </div>
                    <ul className="space-y-3">
                      {data.improvement_tips.map((tip, i) => (
                        <li key={`tip-${i}-${tip.slice(0, 24)}`} className="text-sm text-slate-200 flex gap-3 font-medium leading-relaxed">
                          <span className="text-amber-500 font-bold mt-0.5"><CheckCircle2 size={16} /></span>
                          {tip}
                        </li>
                      ))}
                    </ul>
                  </GlassCard>
                </StaggerItem>
              )}

              <StaggerItem className="flex justify-end pt-2">
                <AnimatedButton
                  onClick={() => setTailorModalOpen(true)}
                  className="shadow-xl"
                  showArrow
                >
                  🪄 Tailor Resume (Costs 10 ⚡)
                </AnimatedButton>
              </StaggerItem>

              <StaggerItem>
                <AskAiPanel match={data} resumeId={selectedResumeId} />
              </StaggerItem>
              
              {tailoredResume && (
                <StaggerItem>
                  <TailoredResumeViewer markdownContent={tailoredResume} pdfBase64={tailoredPdfBase64} />
                </StaggerItem>
              )}
            </StaggerContainer>
          )}

          {history.length > 0 && (
            <GlassCard className="p-0 overflow-hidden" hoverEffect={false}>
              <button
                onClick={() => setShowHistory((v) => !v)}
                className="w-full px-6 py-5 text-left text-sm font-bold text-white hover:bg-slate-900/50 flex items-center justify-between transition-colors"
              >
                <span className="flex items-center gap-2"><FileText size={16} className="text-slate-400" /> Match history ({history.length})</span>
                <span className="text-slate-400 bg-slate-950 border border-slate-700 p-1 rounded-full shadow-sm">
                  {showHistory ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </span>
              </button>
              {showHistory && (
                <div className="overflow-x-auto border-t border-slate-800 bg-slate-950">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-900/50 text-[10px] text-slate-400 font-black uppercase tracking-wider border-b border-slate-800">
                      <tr>
                        <th className="px-6 py-3 text-left">Role</th>
                        <th className="px-6 py-3 text-left">Company</th>
                        <th className="px-6 py-3 text-right">Score</th>
                        <th className="px-6 py-3 text-right">Date</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {history.map((h) => (
                        <tr key={h.match_id} className="hover:bg-slate-900/50/80 transition-colors">
                          <td className="px-6 py-4 font-bold text-slate-100">{h.job_title}</td>
                          <td className="px-6 py-4 text-slate-400 font-medium">{h.company || "-"}</td>
                          <td className="px-6 py-4 text-right">
                            <span className={twMerge(clsx("px-2 py-1 rounded-md text-xs font-bold", h.match_score >= 75 ? "bg-emerald-900/30 text-emerald-400" : h.match_score >= 50 ? "bg-amber-900/30 text-amber-400" : "bg-rose-900/30 text-rose-400"))}>
                              {h.match_score.toFixed(1)}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-right text-slate-400 text-xs font-semibold">{new Date(h.created_at).toLocaleDateString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </GlassCard>
          )}
        </div>
        </ScrollReveal>
      </div>

      {tailorModalOpen && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-950 rounded-3xl shadow-2xl w-full max-w-md p-8 border border-slate-700 animate-fade-in [--animation-delay:0ms]">
            <h3 className="text-2xl font-black text-white tracking-tighter">Tailor your resume</h3>
            <p className="text-sm text-slate-400 mt-2 font-medium leading-relaxed">
              Our AI will rewrite your entire resume to perfectly target this job description.
            </p>
            <div className="bg-blue-900/30 border border-blue-800 rounded-xl p-4 mt-6 mb-6">
              <p className="text-sm text-blue-300 font-bold flex items-center gap-2">
                <span className="text-amber-500">⚡</span> This action costs 10 AI Credits.
              </p>
            </div>
            
            <div className="space-y-4">
              <label className="block text-sm font-bold text-slate-200">Choose a Template / Tone</label>
              
              <div className="space-y-2">
                <label className={twMerge(clsx("flex items-start gap-4 p-4 border rounded-xl cursor-pointer transition-all", templateType === "ats" ? "bg-blue-900/30/50 border-primary shadow-sm" : "hover:bg-slate-900/50 border-slate-700"))}>
                  <input type="radio" name="template" value="ats" checked={templateType === "ats"} onChange={(e) => setTemplateType(e.target.value)} className="mt-1" />
                  <div>
                    <div className="text-sm font-bold text-white">ATS-Optimized (Standard)</div>
                    <div className="text-xs text-slate-400 leading-snug mt-1 font-medium">Focuses on keywords and clean structure. Great for large companies.</div>
                  </div>
                </label>

                <label className={twMerge(clsx("flex items-start gap-4 p-4 border rounded-xl cursor-pointer transition-all", templateType === "executive" ? "bg-blue-900/30/50 border-primary shadow-sm" : "hover:bg-slate-900/50 border-slate-700"))}>
                  <input type="radio" name="template" value="executive" checked={templateType === "executive"} onChange={(e) => setTemplateType(e.target.value)} className="mt-1" />
                  <div>
                    <div className="text-sm font-bold text-white">Executive & Leadership</div>
                    <div className="text-xs text-slate-400 leading-snug mt-1 font-medium">Focuses on business impact, metrics, team sizes, and strategy.</div>
                  </div>
                </label>

                <label className={twMerge(clsx("flex items-start gap-4 p-4 border rounded-xl cursor-pointer transition-all", templateType === "technical" ? "bg-blue-900/30/50 border-primary shadow-sm" : "hover:bg-slate-900/50 border-slate-700"))}>
                  <input type="radio" name="template" value="technical" checked={templateType === "technical"} onChange={(e) => setTemplateType(e.target.value)} className="mt-1" />
                  <div>
                    <div className="text-sm font-bold text-white">Technical / Engineering</div>
                    <div className="text-xs text-slate-400 leading-snug mt-1 font-medium">Heavy emphasis on tech stack, architecture, and methodologies.</div>
                  </div>
                </label>

                <label className={twMerge(clsx("flex items-start gap-4 p-4 border rounded-xl cursor-pointer transition-all", templateType === "creative" ? "bg-blue-900/30/50 border-primary shadow-sm" : "hover:bg-slate-900/50 border-slate-700"))}>
                  <input type="radio" name="template" value="creative" checked={templateType === "creative"} onChange={(e) => setTemplateType(e.target.value)} className="mt-1" />
                  <div>
                    <div className="text-sm font-bold text-white">Outcome-Driven (Creative)</div>
                    <div className="text-xs text-slate-400 leading-snug mt-1 font-medium">Highlights campaigns, portfolios, and direct measurable outcomes.</div>
                  </div>
                </label>
              </div>
            </div>

            {tailorError && (
              <div className="mt-5 p-4 bg-rose-900/30 text-rose-400 border border-rose-800 rounded-xl text-sm font-medium">
                {tailorError}
              </div>
            )}

            <div className="mt-8 flex justify-end gap-3">
              <button 
                onClick={() => setTailorModalOpen(false)}
                disabled={tailoring}
                className="px-5 py-2.5 text-sm font-bold text-slate-300 hover:bg-slate-800 rounded-xl transition-colors"
              >
                Cancel
              </button>
              <AnimatedButton 
                onClick={handleTailor}
                disabled={tailoring}
                showArrow={!tailoring}
              >
                {tailoring ? "Generating..." : "Generate Resume"}
              </AnimatedButton>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
