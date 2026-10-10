"use client";

import { AlertCircle, ArrowUpRight, BarChart3, BriefcaseBusiness, Globe2, LoaderCircle, MapPin, Search, TrendingUp } from "lucide-react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { trackEvent } from "../../lib/analytics";
import { apiGet, apiPostJson } from "../../lib/api";
import type { MarketAnalyzeResponse } from "../../lib/types";

type ResumeItem = { id: number; filename: string; created_at: string };

const MarketDemandChart = dynamic(() => import("../../components/MarketDemandChart"), {
  ssr: false,
  loading: () => <div className="skeleton-block h-full w-full" role="status" aria-label="Loading demand chart" />,
});

function tone(value: string) {
  const normalized = value.toLowerCase();
  if (["critical", "missing", "low confidence"].includes(normalized)) return "border-coral/30 bg-coral/5 text-coral";
  if (["high", "medium"].includes(normalized)) return "border-amber-200 bg-amber-50 text-amber-800";
  if (["proven", "high confidence"].includes(normalized)) return "border-primary/30 bg-primary/5 text-primary";
  return "border-border bg-surface text-muted-foreground";
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
        <header className="border-b border-border pb-7">
          <p className="eyebrow">Market research</p>
          <h1 className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Sample current demand for a target role</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Results reflect configured third-party sources and the analyzed sample, not the entire job market.</p>
        </header>

        <div className="mt-8 grid gap-10 xl:grid-cols-[360px_minmax(0,1fr)]">
          <form onSubmit={analyze} className="surface h-fit p-5 sm:p-6 xl:sticky xl:top-24">
            <div className="flex items-center gap-3"><span className="icon-tile"><Search size={18} /></span><div><p className="data-label">Research query</p><h2 className="font-display mt-1 text-lg font-normal">Market snapshot</h2></div></div>
            <div className="mt-6 grid gap-4">
              <label className="grid gap-2 text-sm font-semibold text-foreground">Target role<input className="field-control" value={targetRole} onChange={(event) => setTargetRole(event.target.value)} /></label>
              <div className="grid gap-4 sm:grid-cols-[1fr_90px] xl:grid-cols-[1fr_90px]">
                <label className="grid gap-2 text-sm font-semibold text-foreground">Location<input className="field-control" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
                <label className="grid gap-2 text-sm font-semibold text-foreground">Country<input className="field-control uppercase" maxLength={2} value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} /></label>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-2">
                <label className="grid gap-2 text-sm font-semibold text-foreground">Experience<select className="field-control" value={experienceLevel} onChange={(event) => setExperienceLevel(event.target.value)}><option value="">Any</option><option value="entry">Entry</option><option value="junior">Junior</option><option value="mid">Mid</option><option value="senior">Senior</option><option value="staff">Staff</option></select></label>
                <label className="grid gap-2 text-sm font-semibold text-foreground">Work mode<select className="field-control" value={remote} onChange={(event) => setRemote(event.target.value)}><option value="any">Any</option><option value="remote">Remote</option><option value="onsite">On-site or hybrid</option></select></label>
              </div>
              <label className="grid gap-2 text-sm font-semibold text-foreground">Resume comparison<select className="field-control" value={resumeId} onChange={(event) => setResumeId(event.target.value)}><option value="">Market only</option>{resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename || `Resume ${resume.id}`}</option>)}</select></label>
              <div className="grid grid-cols-2 gap-4">
                <label className="grid gap-2 text-sm font-semibold text-foreground">Sample size<input className="field-control" type="number" min="5" max="100" value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} /></label>
                <label className="grid gap-2 text-sm font-semibold text-foreground">Days<input className="field-control" type="number" min="1" max="365" value={postedWithinDays} onChange={(event) => setPostedWithinDays(Number(event.target.value))} /></label>
              </div>
              <Button type="submit" className="mt-2 w-full" disabled={loading || !targetRole.trim()}>{loading ? <LoaderCircle size={16} className="animate-spin" /> : <BarChart3 size={16} />}{loading ? "Analyzing..." : "Analyze demand"}</Button>
              <p className="text-center text-xs text-muted-foreground">5 analysis units</p>
            </div>
          </form>

          <div className="min-w-0">
            {error ? <div className="flex gap-3 border border-coral/30 bg-coral/5 p-4 text-sm text-coral" role="alert"><AlertCircle size={18} className="shrink-0" /> {error}</div> : null}
            {loading ? <div className="flex min-h-80 items-center justify-center border-y border-border text-sm text-muted-foreground"><LoaderCircle size={20} className="mr-3 animate-spin text-primary" /> Sampling recent listings and normalizing skills</div> : null}
            {!loading && !data ? <EmptyState icon={Globe2} title="Run a focused market sample" description="Choose a role and location to compare current source coverage, skill demand, and resume evidence." /> : null}

            {data ? (
              <div className="space-y-10">
                <section className="border-b border-border pb-8">
                  <p className="eyebrow">Result summary</p>
                  <p className="mt-3 text-xl font-bold leading-8 text-foreground">{data.summary}</p>
                  <dl className="mt-6 grid gap-4 border-t border-border pt-5 sm:grid-cols-4">
                    <div><dt className="data-label">Listings</dt><dd className="mt-1 text-xl font-semibold">{data.sample_size}</dd></div>
                    <div><dt className="data-label">Confidence</dt><dd className="mt-1 font-bold capitalize text-foreground">{data.confidence}</dd></div>
                    <div><dt className="data-label">Source</dt><dd className="mt-1 font-bold text-foreground">{data.source_provider}</dd></div>
                    <div><dt className="data-label">Response</dt><dd className="mt-1 font-bold text-foreground">{data.from_cache ? "Cached" : "Live"}</dd></div>
                  </dl>
                  {data.warnings.length ? <div className="mt-5 border-l-2 border-primary/30 pl-4 text-sm leading-6 text-amber-800">{data.warnings.join(" ")}</div> : null}
                </section>

                <section>
                  <div className="flex items-end justify-between gap-4"><div><p className="eyebrow">Demand</p><h2 className="font-display mt-2 text-2xl font-normal">Most repeated skills</h2></div><TrendingUp size={20} className="text-primary" /></div>
                  <div className="mt-6 h-[390px] min-w-0">
                    <MarketDemandChart data={chartData} />
                  </div>
                </section>

                <section>
                  <h2 className="font-display text-xl font-normal">Demand table</h2>
                  <div className="mt-5 overflow-x-auto border-y border-border">
                    <table className="w-full min-w-[620px] text-sm"><thead className="text-left text-xs text-muted-foreground"><tr><th className="px-3 py-3">Skill</th><th className="px-3 py-3">Category</th><th className="px-3 py-3 text-right">Listings</th><th className="px-3 py-3 text-right">Demand</th><th className="px-3 py-3 text-right">Importance</th></tr></thead><tbody className="divide-y divide-border">{data.top_skills.slice(0, 20).map((skill) => <tr key={skill.skill}><td className="px-3 py-3 font-bold text-foreground">{skill.skill}</td><td className="px-3 py-3 text-muted-foreground">{skill.category}</td><td className="px-3 py-3 text-right text-muted-foreground">{skill.count}</td><td className="px-3 py-3 text-right font-bold text-primary">{skill.percentage.toFixed(1)}%</td><td className="px-3 py-3 text-right"><span className={`rounded-md border px-2 py-1 text-xs font-bold ${tone(skill.importance)}`}>{skill.importance}</span></td></tr>)}</tbody></table>
                  </div>
                </section>

                <section>
                  <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">Resume comparison</p><h2 className="font-display mt-2 text-2xl font-normal">Evidence gaps</h2></div><Button asChild variant="secondary" size="sm"><Link href="/resume">Update resume</Link></Button></div>
                  <div className="mt-5 divide-y divide-border border-y border-border">
                    {data.resume_gap_analysis.slice(0, 12).map((gap) => <article key={gap.skill} className="grid gap-3 py-4 sm:grid-cols-[1fr_auto_auto] sm:items-center"><div><p className="font-bold text-foreground">{gap.skill}</p><p className="mt-1 text-sm text-muted-foreground">{gap.reason}</p></div><span className={`rounded-md border px-2 py-1 text-xs font-bold ${tone(gap.resume_status)}`}>{gap.resume_status}</span><span className={`rounded-md border px-2 py-1 text-xs font-bold ${tone(gap.priority)}`}>{gap.priority}</span></article>)}
                    {!data.resume_gap_analysis.length ? <p className="py-7 text-sm text-muted-foreground">Connect a resume to compare approved career signals.</p> : null}
                  </div>
                </section>

                {data.recommended_projects.length ? <section><p className="eyebrow">Skill actions</p><h2 className="font-display mt-2 text-2xl font-normal">Project ideas</h2><div className="mt-5 grid gap-5 md:grid-cols-2">{data.recommended_projects.map((project) => <article key={project.title} className="surface p-5"><div className="flex items-start justify-between gap-3"><h3 className="font-semibold text-foreground">{project.title}</h3><span className="text-xs font-bold text-primary">{project.difficulty}</span></div><p className="mt-3 text-sm leading-6 text-muted-foreground">{project.description}</p><p className="mt-4 text-xs font-semibold text-muted-foreground">{project.skills_covered.join(" · ")}</p></article>)}</div></section> : null}

                <section><div className="flex items-end justify-between gap-4"><div><p className="eyebrow">Source sample</p><h2 className="font-display mt-2 text-2xl font-normal">Listings analyzed</h2></div><BriefcaseBusiness size={20} className="text-primary" /></div><div className="mt-5 divide-y divide-border border-y border-border">{data.sample_jobs.map((job) => <a key={`${job.source}-${job.url}-${job.title}`} href={job.url || "#"} target="_blank" rel="noreferrer" className="grid gap-2 py-4 hover:bg-surface sm:grid-cols-[1fr_auto] sm:items-center sm:px-3"><div><p className="font-bold text-foreground">{job.title}</p><p className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground"><span>{job.company || "Unknown company"}</span><span className="flex items-center gap-1"><MapPin size={12} />{job.location || "Location unavailable"}</span><span>{job.source}</span></p></div><ArrowUpRight size={16} className="text-muted-foreground" /></a>)}</div></section>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </main>
  );
}
