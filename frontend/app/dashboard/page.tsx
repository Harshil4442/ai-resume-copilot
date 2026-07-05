"use client";

import { useEffect, useMemo, useState } from "react";
import type { AnalyticsSummary, DashboardMatchCard } from "../../lib/types";
import { apiGet } from "../../lib/api";
import ScoreCard from "../../components/ScoreCard";
import MatchHistoryChart from "../../components/MatchHistoryChart";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import Skeleton from "../../components/ui/Skeleton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import AnimatedButton from "../../components/ui/AnimatedButton";
import { 
  FileText, CheckCircle2, AlertTriangle, ArrowRight, 
  Target, BarChart3, Activity, ArrowUpRight, UserCheck
} from "lucide-react";
import Link from "next/link";
import ScoreRing from "../../components/ui/ScoreRing";

function formatDate(value?: string | null) {
  if (!value) return "No activity yet";
  return new Date(value).toLocaleDateString();
}

function MatchCard({ title, match }: { title: string; match?: DashboardMatchCard | null }) {
  return (
    <GlassCard className="h-full flex flex-col p-5">
      <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-3">{title}</div>
      {match ? (
        <div className="flex-grow flex flex-col justify-between">
          <div>
            <div className="text-sm font-bold text-slate-900 line-clamp-2 leading-snug">
              {match.job_title}
            </div>
            {match.company && (
              <div className="text-xs text-slate-500 mt-1">@ {match.company}</div>
            )}
          </div>
          <div className="flex items-center justify-between gap-3 mt-4 pt-4 border-t border-slate-100">
            <span className="text-3xl font-black text-slate-900 tracking-tighter">{match.match_score.toFixed(1)}</span>
            <span className="text-xs font-semibold text-slate-400">{formatDate(match.created_at)}</span>
          </div>
        </div>
      ) : (
        <div className="text-sm text-slate-400 flex-grow flex items-center justify-center">No match yet</div>
      )}
    </GlassCard>
  );
}

function QuickAction({ href, title, subtitle, icon: Icon }: { href: string; title: string; subtitle: string; icon: any }) {
  return (
    <Link href={href} className="group block">
      <GlassCard className="h-full transition-all duration-300 hover:-translate-y-1 hover:shadow-lg hover:border-primary/30">
        <div className="flex items-start gap-4">
          <div className="p-2 rounded-lg bg-slate-100 text-slate-600 group-hover:bg-primary/10 group-hover:text-primary transition-colors">
            <Icon size={20} />
          </div>
          <div className="flex-grow">
            <div className="flex items-center justify-between gap-3 mb-1">
              <div className="text-sm font-bold text-slate-900">{title}</div>
              <ArrowRight size={14} className="text-slate-400 transition-transform group-hover:translate-x-1 group-hover:text-primary" />
            </div>
            <div className="text-xs text-slate-500 leading-relaxed">{subtitle}</div>
          </div>
        </div>
      </GlassCard>
    </Link>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    apiGet<AnalyticsSummary>("/analytics/summary")
      .then((d) => mounted && setData(d))
      .catch((e) => mounted && setError(e?.message || "Failed to load dashboard"));
    return () => {
      mounted = false;
    };
  }, []);

  const avg = data?.average_match_score ?? 0;
  const avgText = data && data.applications_count > 0 ? `${avg.toFixed(1)}` : "-";
  const profileScore = data?.profile_health?.score ?? data?.profile_completeness ?? 0;
  const resumeQuality = data?.resume_quality;
  const matchOverview = data?.match_overview;
  const recurringGaps = data?.recurring_gaps || [];

  const trendData = useMemo(() => data?.match_history || [], [data]);

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader 
        badge="Command Center"
        title="Your career signal, measured."
        subtitle="Track profile health, resume quality, match performance, and the recurring gaps that matter."
      />

      {error && (
        <FadeIn>
          <div className="text-sm text-red-600 bg-red-50/80 backdrop-blur-md border border-red-200 rounded-xl px-4 py-3 shadow-sm flex items-center gap-2 max-w-2xl mx-auto">
            <AlertTriangle size={16} /> {error}
          </div>
        </FadeIn>
      )}

      {!data && !error && (
        <StaggerContainer className="space-y-10">
          <StaggerItem className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </StaggerItem>
          <StaggerItem className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </StaggerItem>
        </StaggerContainer>
      )}

      {data && (
        <StaggerContainer className="space-y-10">
          {/* Key Metrics */}
          <StaggerItem className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <ScoreCard 
              title="Profile Completeness" 
              value={`${Math.round(profileScore)}%`} 
              subtitle="Profile, resume, and match readiness"
              icon={UserCheck} 
            />
            <ScoreCard 
              title="Avg Match Score" 
              value={avgText} 
              subtitle={`${data.applications_count} matches run`}
              icon={Target} 
            />
            <ScoreCard 
              title="Resumes Parsed" 
              value={data.resume_count ?? 0} 
              subtitle={formatDate(resumeQuality?.latest_resume_date)}
              icon={FileText} 
            />
            <ScoreCard 
              title="Last Activity" 
              value={<Activity size={24} className="text-slate-900" />} 
              subtitle={formatDate(data.activity_summary?.last_activity_at)}
              icon={Activity} 
            />
          </StaggerItem>

          {/* Detailed Health Panels */}
          <StaggerItem className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <GlassCard className="p-6 md:p-8 flex flex-col md:flex-row items-center md:items-start gap-8">
              <div className="flex-shrink-0">
                <ScoreRing score={Math.round(profileScore)} size={140} strokeWidth={10} />
              </div>
              <div className="flex-grow text-center md:text-left">
                <div className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Profile Health</div>
                <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-3">Readiness scan</h2>
                
                <p className="text-sm text-slate-600 mb-6 leading-relaxed">
                  {profileScore >= 80
                    ? "Your profile has enough context for strong analysis. Great job."
                    : "Add a few more profile details to improve analysis context and accuracy."}
                </p>
                
                {(data.profile_health?.missing_items || []).length > 0 ? (
                  <div>
                    <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3 flex items-center justify-center md:justify-start gap-1">
                      <AlertTriangle size={12} className="text-amber-500" /> Action required
                    </div>
                    <div className="flex flex-wrap justify-center md:justify-start gap-2">
                      {(data.profile_health?.missing_items || []).map((item) => (
                        <span key={item} className="inline-flex h-6 items-center rounded-full bg-amber-50 px-2.5 text-[10px] font-bold text-amber-700 border border-amber-200 shadow-sm">
                          {item}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-sm font-semibold text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-full border border-emerald-100 inline-flex">
                    <CheckCircle2 size={16} /> All systems go
                  </div>
                )}
                
                <div className="mt-6 pt-6 border-t border-slate-100 flex justify-center md:justify-start">
                  <AnimatedButton href="/profile" variant="outline" className="h-9 text-xs px-4" showArrow>
                    Update Profile
                  </AnimatedButton>
                </div>
              </div>
            </GlassCard>

            <GlassCard className="p-6 md:p-8">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Resume Quality</div>
              <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-6">Evidence density</h2>
              
              <div className="grid grid-cols-2 gap-y-8 gap-x-4">
                <div>
                  <div className="text-xs font-semibold text-slate-500 mb-1">Unique skills</div>
                  <div className="text-3xl font-black text-slate-900 tracking-tighter">{resumeQuality?.total_unique_skills ?? 0}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-500 mb-1">Verification rate</div>
                  <div className="text-3xl font-black text-slate-900 tracking-tighter">{resumeQuality?.verification_rate ?? 0}%</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-500 mb-1">Evidenced skills</div>
                  <div className="text-3xl font-black text-slate-900 tracking-tighter">{resumeQuality?.evidenced_skills ?? 0}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-500 mb-1">Quantified signals</div>
                  <div className="text-3xl font-black text-slate-900 tracking-tighter">{resumeQuality?.quantified_achievements ?? 0}</div>
                </div>
              </div>
              
              {(resumeQuality?.missing_sections || []).length > 0 && (
                <div className="mt-8 p-4 bg-slate-50 rounded-xl border border-slate-100">
                  <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
                    <AlertTriangle size={12} className="text-amber-500" /> Missing Sections
                  </div>
                  <p className="text-xs text-slate-700 leading-relaxed font-medium">
                    {(resumeQuality?.missing_sections || []).join(", ")}
                  </p>
                </div>
              )}
            </GlassCard>
          </StaggerItem>

          {/* Match Performance Grid */}
          <StaggerItem>
            <h2 className="text-2xl font-black text-slate-900 mb-4 tracking-tighter px-1">Match Performance</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <MatchCard title="Best match" match={matchOverview?.best_match} />
              <MatchCard title="Weakest match" match={matchOverview?.weakest_match} />
              <MatchCard title="Latest match" match={matchOverview?.latest_match} />
            </div>
          </StaggerItem>

          {/* Chart */}
          <StaggerItem>
            <GlassCard className="p-6">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-4">Score Trend</div>
              <MatchHistoryChart data={trendData} />
            </GlassCard>
          </StaggerItem>

          {/* Gaps & Actions */}
          <StaggerItem className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <GlassCard className="p-6 md:p-8 lg:col-span-2">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Recurring Gaps</div>
              <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-4">Skills slowing you down</h2>
              
              {recurringGaps.length > 0 ? (
                <>
                  <p className="text-sm text-slate-500 mb-6 leading-relaxed max-w-xl">
                    These are the skills you frequently lack compared to the job descriptions you apply for. Focus your learning here to boost your match scores across the board.
                  </p>
                  <div className="flex flex-wrap gap-2 mb-8">
                    {recurringGaps.map((gap) => (
                      <span key={gap.skill} className="inline-flex h-7 items-center rounded-full bg-rose-50 px-3 text-xs font-bold text-rose-700 border border-rose-200 shadow-sm transition-transform hover:scale-105">
                        {gap.skill} <span className="ml-1.5 opacity-50 font-normal">· {gap.count}</span>
                      </span>
                    ))}
                  </div>
                  <AnimatedButton href="/learning" variant="secondary" className="h-9 text-xs px-4">
                    Generate learning strategy
                  </AnimatedButton>
                </>
              ) : (
                <div className="py-8 text-center bg-slate-50 rounded-xl border border-slate-100 border-dashed">
                  <p className="text-sm font-medium text-slate-500">Run more job matches to see recurring gap patterns.</p>
                </div>
              )}
            </GlassCard>

            <GlassCard className="p-6 md:p-8 flex flex-col">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Next Best Move</div>
              <h2 className="text-2xl font-black text-slate-900 tracking-tighter mb-6">Quick Actions</h2>
              
              <div className="flex flex-col gap-3 flex-grow justify-center">
                <QuickAction href="/profile" title="Update Profile" subtitle="Add career context" icon={UserCheck} />
                <QuickAction href="/resume" title="Upload Resume" subtitle="Refresh parsed data" icon={FileText} />
                <QuickAction href="/jobs" title="Run Match" subtitle="Analyze a target job" icon={Target} />
              </div>
            </GlassCard>
          </StaggerItem>
        </StaggerContainer>
      )}
    </main>
  );
}


