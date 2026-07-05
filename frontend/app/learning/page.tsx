"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPostJson } from "../../lib/api";
import type {
  JobMatchHistoryItem,
  LearningPriority,
  LearningStrategyResponse,
  MissingHiringSignal,
  ProjectRecommendation,
} from "../../lib/types";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { 
  AlertTriangle, BookOpen, Clock, Compass, Target, 
  Lightbulb, CheckCircle2, ChevronRight, FileText, ArrowRight, Link as LinkIcon
} from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

function badgeClass(value: string) {
  const v = value.toLowerCase();
  if (v === "high") return "bg-rose-50 text-rose-700 border-rose-200";
  if (v === "medium") return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-emerald-50 text-emerald-700 border-emerald-200";
}

function SignalCard({ signal }: { signal: MissingHiringSignal }) {
  return (
    <GlassCard className="p-5">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="text-base font-black text-slate-900 tracking-tight">{signal.signal}</div>
        <span className={twMerge(clsx("text-[10px] font-black uppercase tracking-wider px-2.5 py-1 rounded-md border shadow-sm", badgeClass(signal.severity)))}>
          {signal.severity}
        </span>
      </div>
      <p className="text-sm text-slate-600 leading-relaxed font-medium">{signal.why_it_matters}</p>
    </GlassCard>
  );
}

function PriorityCard({ priority }: { priority: LearningPriority }) {
  return (
    <GlassCard className="p-6 h-full flex flex-col">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="text-lg font-black text-slate-900 tracking-tight">{priority.skill}</div>
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mt-1">{priority.current_status.replaceAll("_", " ")}</div>
        </div>
        <span className={twMerge(clsx("text-[10px] font-black uppercase tracking-wider px-2.5 py-1 rounded-md border shadow-sm", badgeClass(priority.priority)))}>
          {priority.priority} Priority
        </span>
      </div>
      
      <p className="text-sm text-slate-600 leading-relaxed font-medium mb-5 flex-grow">{priority.reason}</p>
      
      <div className="bg-slate-50 rounded-xl p-4 border border-slate-100 mb-5">
        <div className="text-[10px] font-black uppercase tracking-wider text-slate-500 mb-1.5 flex items-center gap-1.5"><Target size={12} className="text-primary" /> Target Outcome</div>
        <div className="text-sm font-semibold text-slate-800">{priority.expected_outcome}</div>
      </div>
      
      {priority.resources?.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-slate-100">
          <div className="text-[10px] font-black text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5"><BookOpen size={12} /> Useful Resources</div>
          {priority.resources.map((r, idx) => (
            <a
              key={`${r.title}-${idx}`}
              href={r.url || "#"}
              target="_blank"
              rel="noreferrer"
              className="group flex flex-col border border-slate-200 rounded-xl px-4 py-3 hover:bg-slate-50 hover:border-primary/30 transition-all"
            >
              <div className="text-sm font-bold text-slate-900 group-hover:text-primary transition-colors line-clamp-1">{r.title}</div>
              <div className="text-xs text-slate-500 font-medium flex items-center justify-between mt-1">
                <span>{r.platform}{r.level ? ` · ${r.level}` : ""}</span>
                <LinkIcon size={12} className="opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            </a>
          ))}
        </div>
      )}
    </GlassCard>
  );
}

function ProjectCard({ project }: { project: ProjectRecommendation }) {
  return (
    <GlassCard className="p-6 md:p-8 space-y-6 border-primary/20 bg-primary/5">
      <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-black text-slate-900 tracking-tighter">{project.title}</h2>
          <p className="text-sm text-slate-600 mt-2 leading-relaxed font-medium max-w-2xl">{project.description}</p>
        </div>
      </div>

      <div>
        <div className="text-[10px] font-black uppercase tracking-wider text-slate-500 mb-2">Gaps Covered</div>
        <div className="flex flex-wrap gap-2">
          {project.covers_gaps.map((gap) => (
            <span key={gap} className="px-3 py-1 rounded-full bg-white border border-blue-200 text-blue-700 text-xs font-bold shadow-sm">
              {gap}
            </span>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-4 border-t border-blue-100/50">
        <div className="bg-white rounded-2xl p-5 border border-slate-100 shadow-sm">
          <div className="text-[10px] font-black text-slate-500 uppercase tracking-wider mb-4 flex items-center gap-1.5"><Compass size={14} className="text-primary" /> Build Steps</div>
          <ul className="space-y-3">
            {project.implementation_steps.map((step, idx) => (
              <li key={`step-${idx}-${step.slice(0, 24)}`} className="text-sm text-slate-700 leading-relaxed font-medium flex gap-2">
                <span className="text-primary font-bold mt-0.5">{idx + 1}.</span>
                {step}
              </li>
            ))}
          </ul>
        </div>
        <div className="bg-white rounded-2xl p-5 border border-slate-100 shadow-sm">
          <div className="text-[10px] font-black text-slate-500 uppercase tracking-wider mb-4 flex items-center gap-1.5"><FileText size={14} className="text-primary" /> Resume Bullets</div>
          <ul className="space-y-3">
            {project.resume_bullets.map((bullet, idx) => (
              <li key={`bullet-${idx}-${bullet.slice(0, 24)}`} className="text-sm text-slate-700 leading-relaxed font-medium flex gap-2">
                <span className="text-slate-300 font-bold mt-0.5">•</span>
                {bullet}
              </li>
            ))}
          </ul>
        </div>
        <div className="bg-white rounded-2xl p-5 border border-slate-100 shadow-sm">
          <div className="text-[10px] font-black text-slate-500 uppercase tracking-wider mb-4 flex items-center gap-1.5"><Lightbulb size={14} className="text-amber-500" /> Interview Points</div>
          <ul className="space-y-3">
            {project.interview_talking_points.map((point, idx) => (
              <li key={`point-${idx}-${point.slice(0, 24)}`} className="text-sm text-slate-700 leading-relaxed font-medium flex gap-2">
                <span className="text-amber-400 font-bold mt-0.5"><CheckCircle2 size={16} /></span>
                {point}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </GlassCard>
  );
}

export default function LearningPage() {
  const [matches, setMatches] = useState<JobMatchHistoryItem[]>([]);
  const [selectedMatchId, setSelectedMatchId] = useState("");
  const [strategy, setStrategy] = useState<LearningStrategyResponse | null>(null);
  const [loadingMatches, setLoadingMatches] = useState(true);
  const [loadingStrategy, setLoadingStrategy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ matches: JobMatchHistoryItem[] }>("/jobs/matches")
      .then((res) => {
        setMatches(res.matches);
        if (res.matches.length > 0) setSelectedMatchId(String(res.matches[0].match_id));
      })
      .catch((e: any) => setError(e?.message || "Failed to load job matches"))
      .finally(() => setLoadingMatches(false));
  }, []);

  const selectedMatch = useMemo(
    () => matches.find((m) => String(m.match_id) === selectedMatchId),
    [matches, selectedMatchId],
  );

  async function generateStrategy() {
    if (!selectedMatchId) return;
    setError(null);
    setStrategy(null);
    setLoadingStrategy(true);
    try {
      const res = await apiPostJson<LearningStrategyResponse>("/recommendations/match_strategy", {
        match_id: Number(selectedMatchId),
      });
      setStrategy(res);
      window.dispatchEvent(new Event("refresh_credits"));
    } catch (e: any) {
      setError(e?.message || "Failed to generate learning strategy");
    } finally {
      setLoadingStrategy(false);
    }
  }

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader 
        badge="Learning Strategy"
        title="Convert match gaps into portfolio proof."
        subtitle="Select a previous job match and generate a project-centered strategy with resume bullets and interview talking points."
      />

      <GlassCard className="p-6 md:p-8" hoverEffect={false}>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-6 items-end">
          <div className="space-y-4">
            <label className="block text-xs font-bold text-slate-700 uppercase tracking-wide">Select Job Match to analyze</label>
            {loadingMatches ? (
              <div className="w-full rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-400 bg-slate-50 animate-pulse">Loading matches...</div>
            ) : matches.length === 0 ? (
              <div className="w-full border border-amber-200 rounded-xl px-4 py-3 text-sm bg-amber-50 flex items-start gap-3">
                <AlertTriangle className="text-amber-500 mt-0.5" size={16} />
                <div>
                  <p className="text-amber-800 font-bold">No job matches found</p>
                  <p className="text-amber-700 text-xs mt-1">Run a job match first, then return here for a learning strategy.</p>
                  <a href="/jobs" className="text-blue-700 font-bold underline text-xs mt-2 inline-block">Go to Job Match Analyzer</a>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <select
                  className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-bold text-slate-800 outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 shadow-sm cursor-pointer"
                  value={selectedMatchId}
                  onChange={(e) => setSelectedMatchId(e.target.value)}
                >
                  {matches.map((m) => (
                    <option key={m.match_id} value={String(m.match_id)}>
                      {m.job_title}{m.company ? ` @ ${m.company}` : ""} ({m.match_score.toFixed(1)}/100) - {new Date(m.created_at).toLocaleDateString()}
                    </option>
                  ))}
                </select>
                
                {selectedMatch && (
                  <div className="flex items-center gap-3 text-xs font-semibold text-slate-500 px-1">
                    <span className="flex items-center gap-1"><Target size={14} /> Match #{selectedMatch.match_id}</span>
                    <span className="w-1 h-1 rounded-full bg-slate-300" />
                    <span>Score: {selectedMatch.match_score.toFixed(1)}/100</span>
                  </div>
                )}
              </div>
            )}
          </div>

          <AnimatedButton
            onClick={generateStrategy}
            disabled={!selectedMatchId || loadingStrategy || loadingMatches}
            className="py-3 px-6 h-12 whitespace-nowrap"
            showArrow
          >
            {loadingStrategy ? "Generating Strategy..." : "Generate Strategy (10 ⚡)"}
          </AnimatedButton>
        </div>

        {error && (
          <FadeIn className="mt-6">
            <div className="text-sm text-rose-600 bg-rose-50 border border-rose-200 rounded-xl px-4 py-3 shadow-sm">{error}</div>
          </FadeIn>
        )}
      </GlassCard>
      
      {loadingStrategy && (
        <FadeIn>
          <GlassCard className="p-16 text-center border-dashed border-slate-300 border-2" hoverEffect={false}>
            <div className="mx-auto w-12 h-12 rounded-full border-4 border-slate-200 border-t-primary animate-spin mb-6"></div>
            <h2 className="text-xl font-bold text-slate-900 tracking-tight">Designing your learning strategy...</h2>
            <p className="text-sm text-slate-500 mt-2">Analyzing gaps, researching resources, and creating custom projects.</p>
          </GlassCard>
        </FadeIn>
      )}

      {strategy && (
        <StaggerContainer className="space-y-10">
          <StaggerItem>
            <GlassCard className="p-8 md:p-10 bg-gradient-to-br from-blue-900 via-slate-900 to-slate-900 border-blue-800 text-white overflow-hidden relative" hoverEffect={false}>
              <div className="absolute top-0 right-0 w-64 h-64 bg-primary/20 blur-3xl rounded-full -mr-20 -mt-20 pointer-events-none"></div>
              
              <div className="relative z-10 flex flex-col md:flex-row items-start md:items-center justify-between gap-8">
                <div className="max-w-2xl">
                  <div className="text-[10px] font-black text-blue-400 uppercase tracking-widest mb-3 flex items-center gap-2">
                    <Target size={14} /> Readiness Plan
                  </div>
                  <h2 className="text-3xl font-black text-white tracking-tighter mb-4 leading-tight">
                    {strategy.job_title}{strategy.company ? ` @ ${strategy.company}` : ""}
                  </h2>
                  <p className="text-slate-300 leading-relaxed font-medium text-sm md:text-base">
                    {strategy.readiness_summary}
                  </p>
                  
                  {strategy.generated_by === "fallback" && (
                    <div className="mt-4 inline-flex items-center gap-2 text-xs font-bold text-amber-300 bg-amber-900/40 px-3 py-1.5 rounded-lg border border-amber-500/30">
                      <AlertTriangle size={14} /> Fallback strategy generated
                    </div>
                  )}
                </div>
                
                <div className="text-left md:text-right flex-shrink-0 bg-white/10 backdrop-blur-md border border-white/10 p-6 rounded-3xl">
                  <div className="text-xs font-bold text-blue-200 uppercase tracking-wider mb-2">Starting Score</div>
                  <div className="text-5xl font-black text-white tracking-tighter">{strategy.current_score.toFixed(1)}</div>
                </div>
              </div>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <div className="flex items-center gap-3 mb-6 px-1">
              <div className="w-10 h-10 rounded-xl bg-rose-100 flex items-center justify-center text-rose-600"><AlertTriangle size={20} /></div>
              <h2 className="text-2xl font-black text-slate-900 tracking-tighter">Missing Hiring Signals</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {strategy.missing_hiring_signals.map((signal, idx) => (
                <SignalCard key={`${signal.signal}-${idx}`} signal={signal} />
              ))}
            </div>
          </StaggerItem>

          <StaggerItem>
            <div className="flex items-center gap-3 mb-6 px-1">
              <div className="w-10 h-10 rounded-xl bg-emerald-100 flex items-center justify-center text-emerald-600"><BookOpen size={20} /></div>
              <h2 className="text-2xl font-black text-slate-900 tracking-tighter">Learning Priorities</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
              {strategy.learning_priorities.map((priority, idx) => (
                <PriorityCard key={`${priority.skill}-${idx}`} priority={priority} />
              ))}
            </div>
          </StaggerItem>

          <StaggerItem className="space-y-6">
            <div className="flex items-center gap-3 mb-2 px-1">
              <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center text-blue-600"><Lightbulb size={20} /></div>
              <h2 className="text-2xl font-black text-slate-900 tracking-tighter">Project Recommendations</h2>
            </div>
            {strategy.project_recommendations.map((project, idx) => (
              <ProjectCard key={`${project.title}-${idx}`} project={project} />
            ))}
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <div className="flex items-center gap-3 mb-8">
                <div className="w-10 h-10 rounded-xl bg-purple-100 flex items-center justify-center text-purple-600"><Clock size={20} /></div>
                <h2 className="text-xl font-black text-slate-900 tracking-tighter">Suggested Timeline</h2>
              </div>
              
              <div className="relative">
                <div className="absolute left-[27px] md:left-[27px] top-4 bottom-4 w-0.5 bg-slate-200"></div>
                <div className="space-y-8">
                  {strategy.timeline.map((item, idx) => (
                    <div key={`${item.phase}-${idx}`} className="relative pl-16">
                      <div className="absolute left-4 top-1.5 w-6 h-6 rounded-full bg-white border-4 border-slate-200 flex items-center justify-center z-10 shadow-sm">
                        <div className="w-2 h-2 rounded-full bg-primary"></div>
                      </div>
                      
                      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-5 shadow-sm transition-transform hover:-translate-y-1">
                        <div className="text-[10px] font-black text-primary uppercase tracking-widest mb-1">{item.phase}</div>
                        <div className="text-lg font-black text-slate-900 tracking-tight mb-2">{item.focus}</div>
                        <p className="text-sm text-slate-600 font-medium leading-relaxed bg-white p-4 rounded-xl border border-slate-100 mt-3">
                          {item.deliverable}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </GlassCard>
          </StaggerItem>
        </StaggerContainer>
      )}
    </main>
  );
}
