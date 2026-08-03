"use client";

import { AlertCircle, ArrowUpRight, BarChart3, BriefcaseBusiness, Globe2, LoaderCircle, MapPin, Search, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { trackEvent } from "../../lib/analytics";
import { apiGet, apiPostJson } from "../../lib/api";
import type { MarketAnalyzeResponse } from "../../lib/types";

type ResumeItem = { id: number; filename: string; created_at: string };

function tone(value: string) {
  const normalized = value.toLowerCase();
  if (["critical", "missing", "low confidence"].includes(normalized)) return "border-coral/30 bg-coral/5 text-[#ffab9e]";
  if (["high", "medium"].includes(normalized)) return "border-accent/30 bg-accent/5 text-[#ffd075]";
  if (["proven", "high confidence"].includes(normalized)) return "border-primary/30 bg-primary/5 text-[#69debd]";
  return "border-white/10 bg-white/[0.025] text-neutral-400";
}

export default function MarketPage() {
  const [resumes, setResumes] = useState<ResumeItem[]>([]);
  const [targetRole, setTargetRole] = useState("Software Engineer");
  const [location, setLocation] = useState("India");
  const [countryCode, setCountryCode] = useState("IN");
  const [experienceLevel, setExperienceLevel] = useState("mid");
  const [remote, setRemote] = useState("any");
  const [resumeId, setResumeId] = useState("");
  const [maxResults, setMaxResults] = useState(50);
  const [postedWithinDays, setPostedWithinDays] = useState(30);
  const [data, setData] = useState<MarketAnalyzeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ resumes: ResumeItem[] }>("/resume/list")
      .then((response) => {
        setResumes(response.resumes);
        setResumeId(response.resumes[0] ? String(response.resumes[0].id) : "");
      })
      .catch(() => setResumes([]));
  }, []);

  const chartData = useMemo(
    () => (data?.top_skills || []).slice(0, 12).map((item) => ({ skill: item.skill, demand: item.percentage })),
    [data],
  );

  async function analyze(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setData(null);
    setLoading(true);
    trackEvent("market_analysis_started", { country_code: countryCode, has_resume: Boolean(resumeId) });
    try {
      const response = await apiPostJson<MarketAnalyzeResponse>("/market/analyze", {
        target_role: targetRole,
        location,
        country_code: countryCode,
        experience_level: experienceLevel,
        remote: remote === "any" ? null : remote === "remote",
        resume_id: resumeId ? Number(resumeId) : null,
        max_results: maxResults,
        posted_within_days: postedWithinDays,
      });
      setData(response);
      trackEvent("market_analysis_completed", {
        country_code: countryCode,
        sample_size: response.sample_size,
        source_provider: response.source_provider,
      });
      window.dispatchEvent(new Event("refresh_analysis_units"));
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : "Market analysis failed");
      trackEvent("market_analysis_failed", { country_code: countryCode });
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="border-b border-white/10 pb-7">
          <p className="eyebrow">Market research</p>
          <h1 className="mt-2 text-3xl font-black text-neutral-100 sm:text-4xl">Sample current demand for a target role</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-500">Results reflect configured third-party sources and the analyzed sample, not the entire job market.</p>
        </header>

        <div className="mt-8 grid gap-10 xl:grid-cols-[360px_minmax(0,1fr)]">
          <form onSubmit={analyze} className="surface h-fit p-5 sm:p-6 xl:sticky xl:top-24">
            <div className="flex items-center gap-3"><span className="icon-tile"><Search size={18} /></span><div><p className="data-label">Research query</p><h2 className="mt-1 text-lg font-black">Market snapshot</h2></div></div>
            <div className="mt-6 grid gap-4">
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Target role<input className="field-control" value={targetRole} onChange={(event) => setTargetRole(event.target.value)} /></label>
              <div className="grid gap-4 sm:grid-cols-[1fr_90px] xl:grid-cols-[1fr_90px]">
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">Location<input className="field-control" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">Country<input className="field-control uppercase" maxLength={2} value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} /></label>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-2">
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">Experience<select className="field-control" value={experienceLevel} onChange={(event) => setExperienceLevel(event.target.value)}><option value="">Any</option><option value="entry">Entry</option><option value="junior">Junior</option><option value="mid">Mid</option><option value="senior">Senior</option><option value="staff">Staff</option></select></label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">Work mode<select className="field-control" value={remote} onChange={(event) => setRemote(event.target.value)}><option value="any">Any</option><option value="remote">Remote</option><option value="onsite">On-site or hybrid</option></select></label>
              </div>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Resume comparison<select className="field-control" value={resumeId} onChange={(event) => setResumeId(event.target.value)}><option value="">Market only</option>{resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename || `Resume ${resume.id}`}</option>)}</select></label>
              <div className="grid grid-cols-2 gap-4">
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">Sample size<input className="field-control" type="number" min="5" max="100" value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} /></label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">Days<input className="field-control" type="number" min="1" max="365" value={postedWithinDays} onChange={(event) => setPostedWithinDays(Number(event.target.value))} /></label>
              </div>
              <Button type="submit" className="mt-2 w-full" disabled={loading || !targetRole.trim()}>{loading ? <LoaderCircle size={16} className="animate-spin" /> : <BarChart3 size={16} />}{loading ? "Analyzing..." : "Analyze demand"}</Button>
              <p className="text-center text-xs text-neutral-600">5 analysis units</p>
            </div>
          </form>

          <div className="min-w-0">
            {error ? <div className="flex gap-3 border border-coral/30 bg-coral/5 p-4 text-sm text-[#ffab9e]" role="alert"><AlertCircle size={18} className="shrink-0" /> {error}</div> : null}
            {loading ? <div className="flex min-h-80 items-center justify-center border-y border-white/10 text-sm text-neutral-500"><LoaderCircle size={20} className="mr-3 animate-spin text-primary" /> Sampling recent listings and normalizing skills</div> : null}
            {!loading && !data ? <EmptyState icon={Globe2} title="Run a focused market sample" description="Choose a role and location to compare current source coverage, skill demand, and resume evidence." /> : null}

            {data ? (
              <div className="space-y-10">
                <section className="border-b border-white/10 pb-8">
                  <p className="eyebrow">Result summary</p>
                  <p className="mt-3 text-xl font-bold leading-8 text-neutral-200">{data.summary}</p>
                  <dl className="mt-6 grid gap-4 border-t border-white/10 pt-5 sm:grid-cols-4">
                    <div><dt className="data-label">Listings</dt><dd className="mt-1 text-xl font-black">{data.sample_size}</dd></div>
                    <div><dt className="data-label">Confidence</dt><dd className="mt-1 font-bold capitalize text-neutral-300">{data.confidence}</dd></div>
                    <div><dt className="data-label">Source</dt><dd className="mt-1 font-bold text-neutral-300">{data.source_provider}</dd></div>
                    <div><dt className="data-label">Response</dt><dd className="mt-1 font-bold text-neutral-300">{data.from_cache ? "Cached" : "Live"}</dd></div>
                  </dl>
                  {data.warnings.length ? <div className="mt-5 border-l-2 border-accent pl-4 text-sm leading-6 text-[#ffd075]">{data.warnings.join(" ")}</div> : null}
                </section>

                <section>
                  <div className="flex items-end justify-between gap-4"><div><p className="eyebrow">Demand</p><h2 className="mt-2 text-2xl font-black">Most repeated skills</h2></div><TrendingUp size={20} className="text-primary" /></div>
                  <div className="mt-6 h-[390px] min-w-0">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={chartData} layout="vertical" margin={{ left: 30, right: 20 }}>
                        <CartesianGrid horizontal={false} stroke="rgba(255,255,255,0.08)" />
                        <XAxis type="number" domain={[0, 100]} tickFormatter={(value) => `${value}%`} tick={{ fill: "#737a76", fontSize: 11 }} axisLine={false} />
                        <YAxis type="category" dataKey="skill" width={120} tick={{ fill: "#b7bdb9", fontSize: 11 }} axisLine={false} tickLine={false} />
                        <Tooltip formatter={(value) => [`${Number(value).toFixed(1)}%`, "Demand"]} contentStyle={{ background: "#171a19", border: "1px solid rgba(255,255,255,.12)", borderRadius: 8 }} />
                        <Bar dataKey="demand" fill="#42cdaa" radius={[0, 4, 4, 0]} barSize={22} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </section>

                <section>
                  <h2 className="text-xl font-black">Demand table</h2>
                  <div className="mt-5 overflow-x-auto border-y border-white/10">
                    <table className="w-full min-w-[620px] text-sm"><thead className="text-left text-xs text-neutral-600"><tr><th className="px-3 py-3">Skill</th><th className="px-3 py-3">Category</th><th className="px-3 py-3 text-right">Listings</th><th className="px-3 py-3 text-right">Demand</th><th className="px-3 py-3 text-right">Importance</th></tr></thead><tbody className="divide-y divide-white/10">{data.top_skills.slice(0, 20).map((skill) => <tr key={skill.skill}><td className="px-3 py-3 font-bold text-neutral-200">{skill.skill}</td><td className="px-3 py-3 text-neutral-500">{skill.category}</td><td className="px-3 py-3 text-right text-neutral-400">{skill.count}</td><td className="px-3 py-3 text-right font-bold text-primary">{skill.percentage.toFixed(1)}%</td><td className="px-3 py-3 text-right"><span className={`rounded-md border px-2 py-1 text-xs font-bold ${tone(skill.importance)}`}>{skill.importance}</span></td></tr>)}</tbody></table>
                  </div>
                </section>

                <section>
                  <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">Resume comparison</p><h2 className="mt-2 text-2xl font-black">Evidence gaps</h2></div><Button asChild variant="secondary" size="sm"><Link href="/resume">Update resume</Link></Button></div>
                  <div className="mt-5 divide-y divide-white/10 border-y border-white/10">
                    {data.resume_gap_analysis.slice(0, 12).map((gap) => <article key={gap.skill} className="grid gap-3 py-4 sm:grid-cols-[1fr_auto_auto] sm:items-center"><div><p className="font-bold text-neutral-200">{gap.skill}</p><p className="mt-1 text-sm text-neutral-500">{gap.reason}</p></div><span className={`rounded-md border px-2 py-1 text-xs font-bold ${tone(gap.resume_status)}`}>{gap.resume_status}</span><span className={`rounded-md border px-2 py-1 text-xs font-bold ${tone(gap.priority)}`}>{gap.priority}</span></article>)}
                    {!data.resume_gap_analysis.length ? <p className="py-7 text-sm text-neutral-600">Connect a resume to compare approved career signals.</p> : null}
                  </div>
                </section>

                {data.recommended_projects.length ? <section><p className="eyebrow">Skill actions</p><h2 className="mt-2 text-2xl font-black">Project ideas</h2><div className="mt-5 grid gap-5 md:grid-cols-2">{data.recommended_projects.map((project) => <article key={project.title} className="surface p-5"><div className="flex items-start justify-between gap-3"><h3 className="font-black text-neutral-200">{project.title}</h3><span className="text-xs font-bold text-accent">{project.difficulty}</span></div><p className="mt-3 text-sm leading-6 text-neutral-500">{project.description}</p><p className="mt-4 text-xs font-semibold text-neutral-600">{project.skills_covered.join(" · ")}</p></article>)}</div></section> : null}

                <section><div className="flex items-end justify-between gap-4"><div><p className="eyebrow">Source sample</p><h2 className="mt-2 text-2xl font-black">Listings analyzed</h2></div><BriefcaseBusiness size={20} className="text-accent" /></div><div className="mt-5 divide-y divide-white/10 border-y border-white/10">{data.sample_jobs.map((job) => <a key={`${job.source}-${job.url}-${job.title}`} href={job.url || "#"} target="_blank" rel="noreferrer" className="grid gap-2 py-4 hover:bg-white/[0.02] sm:grid-cols-[1fr_auto] sm:items-center sm:px-3"><div><p className="font-bold text-neutral-200">{job.title}</p><p className="mt-1 flex flex-wrap gap-3 text-xs text-neutral-600"><span>{job.company || "Unknown company"}</span><span className="flex items-center gap-1"><MapPin size={12} />{job.location || "Location unavailable"}</span><span>{job.source}</span></p></div><ArrowUpRight size={16} className="text-neutral-600" /></a>)}</div></section>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </main>
  );
}
