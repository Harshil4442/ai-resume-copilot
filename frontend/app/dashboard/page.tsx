"use client";

import { useEffect, useMemo, useState } from "react";
import type { AnalyticsSummary, DashboardMatchCard } from "../../lib/types";
import { apiGet } from "../../lib/api";
import ScoreCard from "../../components/ScoreCard";
import MatchHistoryChart from "../../components/MatchHistoryChart";

function formatDate(value?: string | null) {
  if (!value) return "No activity yet";
  return new Date(value).toLocaleDateString();
}

function MatchCard({ title, match }: { title: string; match?: DashboardMatchCard | null }) {
  return (
    <div className="metric-card kinetic-border tilt-lift">
      <div className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">{title}</div>
      {match ? (
        <div className="mt-2">
          <div className="text-sm font-black text-slate-950">
            {match.job_title}{match.company ? ` @ ${match.company}` : ""}
          </div>
          <div className="flex items-center justify-between gap-3 mt-2">
            <span className="text-3xl font-black ink-gradient">{match.match_score.toFixed(1)}</span>
            <span className="text-xs font-semibold text-slate-500">{formatDate(match.created_at)}</span>
          </div>
        </div>
      ) : (
        <div className="text-sm text-slate-500 mt-2">No match yet</div>
      )}
    </div>
  );
}

function QuickAction({ href, title, subtitle }: { href: string; title: string; subtitle: string }) {
  return (
    <a href={href} className="group block metric-card tilt-lift">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-black text-slate-950">{title}</div>
        <span className="text-blue-600 transition group-hover:translate-x-1">→</span>
      </div>
      <div className="text-xs text-slate-500 mt-1">{subtitle}</div>
    </a>
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
    // Mount-only fetch; `apiGet` is a stable module import.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const avg = data?.average_match_score ?? 0;
  const avgText = data && data.applications_count > 0 ? `${avg.toFixed(1)} / 100` : "-";
  const profileScore = data?.profile_health?.score ?? data?.profile_completeness ?? 0;
  const resumeQuality = data?.resume_quality;
  const matchOverview = data?.match_overview;
  const recurringGaps = data?.recurring_gaps || [];

  const trendData = useMemo(() => data?.match_history || [], [data]);

  return (
    <main className="app-shell space-y-8">
      <section className="product-hero text-left flex items-end justify-between gap-6 flex-wrap">
        <div className="max-w-2xl">
          <div className="label-kicker flex items-center gap-3"><span className="pulse-dot" />Command Center</div>
          <h1 className="text-5xl md:text-7xl font-black leading-[0.88] mt-4 text-slate-950">Your career signal, measured.</h1>
          <p className="text-slate-600 mt-4 leading-relaxed">
            Track profile health, resume quality, match performance, and the recurring gaps that matter.
          </p>
        </div>
        <div className="premium-card min-w-[260px] p-5">
          <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Profile readiness</div>
          <div className="mt-3 text-6xl font-black ink-gradient">{Math.round(profileScore)}%</div>
          <div className="mt-4 h-2 rounded-full bg-slate-100 overflow-hidden">
            <div className="h-full rounded-full bg-gradient-to-r from-blue-400 to-emerald-300" style={{ width: `${profileScore}%` }} />
          </div>
          <a href="/profile" className="btn-primary mt-5 w-full">Edit Profile</a>
        </div>
      </section>

      {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}
      {!data && !error && <div className="text-sm text-gray-600">Loading...</div>}

      {data && (
        <>
          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <ScoreCard title="Profile completeness" value={`${profileScore}%`} subtitle="Profile, resume, and match readiness" />
            <ScoreCard title="Average match score" value={avgText} subtitle={`${data.applications_count} matches run`} />
            <ScoreCard title="Resumes parsed" value={`${data.resume_count ?? 0}`} subtitle={formatDate(resumeQuality?.latest_resume_date)} />
            <ScoreCard title="Last activity" value={formatDate(data.activity_summary?.last_activity_at)} subtitle="Latest resume or match event" />
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="panel kinetic-border p-5">
              <div className="label-kicker">Profile Health</div>
              <h2 className="text-2xl font-black text-slate-950 mt-1">Readiness scan</h2>
              <div className="mt-4 h-2 rounded-full bg-slate-100 overflow-hidden">
                <div className="h-full rounded-full bg-gradient-to-r from-blue-600 to-emerald-400" style={{ width: `${profileScore}%` }} />
              </div>
              <div className="text-sm text-slate-600 mt-3">
                {profileScore >= 80
                  ? "Your profile has enough context for strong analysis."
                  : "Add a few more profile details to improve analysis context."}
              </div>
              {(data.profile_health?.missing_items || []).length > 0 && (
                <div className="mt-4">
                  <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Missing or incomplete</div>
                  <div className="flex flex-wrap gap-2">
                    {(data.profile_health?.missing_items || []).map((item) => <span key={item} className="signal-chip bg-amber-50 text-amber-700 border-amber-100">{item}</span>)}
                  </div>
                </div>
              )}
            </div>

            <div className="panel kinetic-border p-5">
              <div className="label-kicker">Resume Quality</div>
              <h2 className="text-2xl font-black text-slate-950 mt-1">Evidence density</h2>
              <div className="grid grid-cols-2 gap-4 mt-4">
                <div>
                  <div className="text-xs font-semibold text-slate-500">Unique skills</div>
                  <div className="text-3xl font-black ink-gradient">{resumeQuality?.total_unique_skills ?? 0}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-500">Verification rate</div>
                  <div className="text-3xl font-black ink-gradient">{resumeQuality?.verification_rate ?? 0}%</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-500">Evidenced skills</div>
                  <div className="text-3xl font-black ink-gradient">{resumeQuality?.evidenced_skills ?? 0}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-500">Quantified signals</div>
                  <div className="text-3xl font-black ink-gradient">{resumeQuality?.quantified_achievements ?? 0}</div>
                </div>
              </div>
              {(resumeQuality?.missing_sections || []).length > 0 && (
                <div className="mt-4 text-xs text-gray-500">
                  Missing sections: {(resumeQuality?.missing_sections || []).join(", ")}
                </div>
              )}
            </div>
          </section>

          <section>
            <h2 className="text-2xl font-black text-slate-950 mb-3">Match Performance</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <MatchCard title="Best match" match={matchOverview?.best_match} />
              <MatchCard title="Weakest match" match={matchOverview?.weakest_match} />
              <MatchCard title="Latest match" match={matchOverview?.latest_match} />
            </div>
          </section>

          <MatchHistoryChart data={trendData} />

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="panel kinetic-border p-5">
              <div className="label-kicker">Recurring Gaps</div>
              <h2 className="text-2xl font-black text-slate-950 mt-1">Skills slowing you down</h2>
              {recurringGaps.length > 0 ? (
                <div className="flex flex-wrap gap-2 mt-4">
                  {recurringGaps.map((gap) => (
                    <span key={gap.skill} className="signal-chip bg-red-50 text-red-700 border-red-100">
                      {gap.skill} · {gap.count}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-500 mt-3">Run more job matches to see recurring gap patterns.</p>
              )}
              <a href="/learning" className="inline-block text-sm text-blue-600 hover:underline mt-4">
                Generate a learning strategy
              </a>
            </div>

            <div className="panel kinetic-border p-5">
              <div className="label-kicker">Quick Actions</div>
              <h2 className="text-2xl font-black text-slate-950 mt-1">Next best move</h2>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-4">
                <QuickAction href="/profile" title="Update Profile" subtitle="Add career context" />
                <QuickAction href="/resume" title="Upload Resume" subtitle="Refresh parsed data" />
                <QuickAction href="/jobs" title="Run Match" subtitle="Analyze a target job" />
              </div>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
