"use client";

import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet, apiPostJson } from "../../lib/api";
import type { MarketAnalyzeResponse, MarketResumeGap, MarketTopSkill } from "../../lib/types";

type ResumeItem = {
  id: number;
  filename: string;
  created_at: string;
};

function badgeClass(value: string) {
  const v = value.toLowerCase();
  if (v === "critical") return "bg-red-50 text-red-700 border-red-200";
  if (v === "high") return "bg-orange-50 text-orange-700 border-orange-200";
  if (v === "medium") return "bg-amber-50 text-amber-700 border-amber-200";
  if (v === "proven" || v === "high confidence") return "bg-green-50 text-green-700 border-green-200";
  if (v === "claimed") return "bg-blue-50 text-blue-700 border-blue-200";
  if (v === "missing" || v === "low confidence") return "bg-red-50 text-red-700 border-red-200";
  return "bg-gray-50 text-gray-700 border-gray-200";
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rail-card tilt-lift">
      <div className="text-[11px] uppercase tracking-wide text-blue-100">{label}</div>
      <div className="text-2xl font-black text-white mt-1">{value}</div>
      {sub && <div className="text-xs text-blue-100 mt-1">{sub}</div>}
    </div>
  );
}

function SkillBadge({ skill }: { skill: string }) {
  return <span className="signal-chip">{skill}</span>;
}

function TopSkillsTable({ skills }: { skills: MarketTopSkill[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs uppercase tracking-wide text-gray-500 border-b border-gray-100">
          <tr>
            <th className="text-left py-2 pr-3">Skill</th>
            <th className="text-left py-2 pr-3">Category</th>
            <th className="text-right py-2 pr-3">Count</th>
            <th className="text-right py-2 pr-3">Demand</th>
            <th className="text-right py-2">Importance</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {skills.map((skill) => (
            <tr key={skill.skill}>
              <td className="py-3 pr-3 font-semibold text-gray-900">{skill.skill}</td>
              <td className="py-3 pr-3 text-gray-500">{skill.category}</td>
              <td className="py-3 pr-3 text-right">{skill.count}</td>
              <td className="py-3 pr-3 text-right font-semibold">{skill.percentage.toFixed(1)}%</td>
              <td className="py-3 text-right">
                <span className={`px-2 py-1 rounded-lg border text-xs font-semibold ${badgeClass(skill.importance)}`}>
                  {skill.importance}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GapRow({ gap }: { gap: MarketResumeGap }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_120px_120px_1.6fr] gap-3 panel p-4">
      <div>
        <div className="text-sm font-bold text-gray-900">{gap.skill}</div>
        <div className="text-xs text-gray-500 mt-1">Demand {gap.market_demand_percentage.toFixed(1)}%</div>
      </div>
      <div>
        <span className={`px-2 py-1 rounded-lg border text-xs font-semibold ${badgeClass(gap.resume_status)}`}>{gap.resume_status}</span>
      </div>
      <div>
        <span className={`px-2 py-1 rounded-lg border text-xs font-semibold ${badgeClass(gap.priority)}`}>{gap.priority}</span>
      </div>
      <p className="text-sm text-gray-600 leading-relaxed">{gap.reason}</p>
    </div>
  );
}

export default function MarketPage() {
  const [resumes, setResumes] = useState<ResumeItem[]>([]);
  const [targetRole, setTargetRole] = useState("Backend Engineer");
  const [location, setLocation] = useState("United States");
  const [countryCode, setCountryCode] = useState("US");
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
      .then((res) => {
        setResumes(res.resumes);
        if (res.resumes.length > 0) setResumeId(String(res.resumes[0].id));
      })
      .catch(() => {});
    // Mount-only fetch; `apiGet` is a stable module import.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const chartData = useMemo(
    () => (data?.top_skills || []).slice(0, 12).map((item) => ({
      skill: item.skill,
      demand: item.percentage,
    })),
    [data],
  );

  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setData(null);
    setLoading(true);
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
    } catch (err: any) {
      setError(err?.message || "Market analysis failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell space-y-8">
      <section className="dark-panel hero-stage live-grid scanline kinetic-border overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="p-6 md:p-8">
            <div className="label-kicker text-blue-200 flex items-center gap-3"><span className="pulse-dot" />Market Skills Intelligence</div>
            <h1 className="text-5xl md:text-7xl font-black mt-4 max-w-4xl leading-[0.88] holo-text">
              See what the job market is asking for right now.
            </h1>
            <p className="text-sm md:text-base text-blue-100 mt-4 max-w-2xl leading-relaxed">
              Search worldwide job APIs, extract repeated skills, compare demand against your resume evidence, and pick projects that close high-value gaps.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-8">
              <Stat label="Mode" value="Live" sub="API-backed snapshot" />
              <Stat label="Cache" value="Redis" sub="Optional via REDIS_URL" />
              <Stat label="Analysis" value="Deterministic" sub="LLM-safe foundation" />
            </div>
          </div>
          <div className="bg-white/95 text-gray-950 p-6 md:p-8 border-t lg:border-t-0 lg:border-l border-white/20 backdrop-blur">
            <form onSubmit={analyze} className="space-y-4">
              <div>
                <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Target role</label>
                <input className="field" value={targetRole} onChange={(e) => setTargetRole(e.target.value)} />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Location</label>
                  <input className="field" value={location} onChange={(e) => setLocation(e.target.value)} />
                </div>
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Country</label>
                  <input className="field uppercase" maxLength={2} value={countryCode} onChange={(e) => setCountryCode(e.target.value.toUpperCase())} />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Experience</label>
                  <select className="field" value={experienceLevel} onChange={(e) => setExperienceLevel(e.target.value)}>
                    <option value="">Any</option>
                    <option value="entry">Entry</option>
                    <option value="junior">Junior</option>
                    <option value="mid">Mid</option>
                    <option value="senior">Senior</option>
                    <option value="staff">Staff</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Work mode</label>
                  <select className="field" value={remote} onChange={(e) => setRemote(e.target.value)}>
                    <option value="any">Any</option>
                    <option value="remote">Remote only</option>
                    <option value="onsite">On-site or hybrid</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Compare resume</label>
                <select className="field" value={resumeId} onChange={(e) => setResumeId(e.target.value)}>
                  <option value="">No resume comparison</option>
                  {resumes.map((resume) => (
                    <option key={resume.id} value={String(resume.id)}>
                      {resume.filename || `Resume #${resume.id}`} - {new Date(resume.created_at).toLocaleDateString()}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Sample size</label>
                  <input className="field" type="number" min="5" max="100" value={maxResults} onChange={(e) => setMaxResults(Number(e.target.value))} />
                </div>
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">Posted within</label>
                  <input className="field" type="number" min="1" max="365" value={postedWithinDays} onChange={(e) => setPostedWithinDays(Number(e.target.value))} />
                </div>
              </div>
              <button disabled={loading || !targetRole.trim()} className="btn-primary w-full">
                {loading ? "Analyzing live market..." : "Analyze Market Demand"}
              </button>
            </form>
          </div>
        </div>
      </section>

      {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}

      {!data && !loading && (
        <section className="panel kinetic-border p-8 text-center">
          <h2 className="text-3xl font-black text-slate-950 ink-gradient">Ready for a live market snapshot</h2>
          <p className="text-sm text-gray-500 mt-2 max-w-xl mx-auto">
            Configure a provider API key on the backend, then analyze role-specific skills across current postings.
          </p>
          <div className="mt-5 grid grid-cols-1 md:grid-cols-3 gap-3 text-left">
            <div className="metric-card tilt-lift">
              <div className="text-sm font-bold">TheirStack first</div>
              <p className="text-xs text-gray-500 mt-1">Best fit for worldwide job search and historical expansion.</p>
            </div>
            <div className="metric-card tilt-lift">
              <div className="text-sm font-bold">Adzuna fallback</div>
              <p className="text-xs text-gray-500 mt-1">Official job search API with employment data endpoints.</p>
            </div>
            <div className="metric-card tilt-lift">
              <div className="text-sm font-bold">Jooble fallback</div>
              <p className="text-xs text-gray-500 mt-1">Global aggregator fallback when configured.</p>
            </div>
          </div>
        </section>
      )}

      {data && (
        <div className="space-y-8">
          <section className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-5">
            <div className="panel kinetic-border p-5">
              <div className="text-xs font-bold uppercase tracking-wide text-gray-500">Summary</div>
              <p className="text-xl font-black text-gray-950 mt-2 leading-snug">{data.summary}</p>
            </div>
            <div className="dark-panel hero-stage p-5">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs text-blue-100">Sample</div>
                  <div className="text-2xl font-black">{data.sample_size}</div>
                </div>
                <div>
                  <div className="text-xs text-blue-100">Confidence</div>
                  <div className="text-2xl font-black capitalize">{data.confidence}</div>
                </div>
                <div>
                  <div className="text-xs text-blue-100">Source</div>
                  <div className="text-sm font-bold">{data.source_provider}</div>
                </div>
                <div>
                  <div className="text-xs text-blue-100">Cache</div>
                  <div className="text-sm font-bold">{data.from_cache ? "Hit" : "Live"}</div>
                </div>
              </div>
            </div>
          </section>

          {data.warnings.length > 0 && (
            <section className="rounded-lg border border-amber-200 bg-amber-50 p-5">
              <div className="text-sm font-bold text-amber-800">Warnings</div>
              <ul className="mt-2 space-y-1">
                {data.warnings.map((warning) => (
                  <li key={warning} className="text-sm text-amber-700">- {warning}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="grid grid-cols-1 xl:grid-cols-[1.2fr_0.8fr] gap-5">
            <div className="panel kinetic-border p-5">
              <div className="flex items-center justify-between gap-4 mb-4">
                <div>
                  <h2 className="text-lg font-black text-gray-950">Top demanded skills</h2>
                  <p className="text-sm text-gray-500">Percentage means how many sampled postings mentioned the skill.</p>
                </div>
              </div>
              <div className="h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical" margin={{ left: 40, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
                    <YAxis type="category" dataKey="skill" width={110} tick={{ fontSize: 12 }} />
                    <Tooltip formatter={(value: number) => [`${Number(value).toFixed(1)}%`, "Demand"]} />
                    <Bar dataKey="demand" fill="#2563eb" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="panel kinetic-border p-5">
              <h2 className="text-lg font-black text-gray-950">Category breakdown</h2>
              <div className="space-y-4 mt-4 max-h-80 overflow-y-auto pr-1">
                {data.skill_categories.map((category) => (
                  <div key={category.category}>
                    <div className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-2">{category.category}</div>
                    <div className="flex flex-wrap gap-2">
                      {category.skills.slice(0, 8).map((skill) => (
                        <span key={skill.skill} className="px-2 py-1 rounded-lg bg-gray-100 text-xs text-gray-700">
                          {skill.skill} {skill.percentage.toFixed(0)}%
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="panel kinetic-border p-5">
            <h2 className="text-lg font-black text-gray-950 mb-4">Skill demand table</h2>
            <TopSkillsTable skills={data.top_skills.slice(0, 20)} />
          </section>

          <section>
            <div className="flex items-end justify-between gap-4 mb-4">
              <div>
                <h2 className="text-2xl font-black text-slate-950">Resume vs market</h2>
                <p className="text-sm text-gray-500">High-demand skills are classified as proven, claimed, or missing.</p>
              </div>
              <a href="/resume" className="text-sm font-semibold text-blue-600 hover:underline">Update resume</a>
            </div>
            <div className="space-y-3">
              {data.resume_gap_analysis.length > 0 ? (
                data.resume_gap_analysis.slice(0, 12).map((gap) => <GapRow key={gap.skill} gap={gap} />)
              ) : (
                <div className="panel p-5 text-sm text-gray-500">No gap analysis available.</div>
              )}
            </div>
          </section>

          <section>
            <h2 className="text-2xl font-black text-slate-950 mb-4">Project ideas to close market gaps</h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {data.recommended_projects.map((project) => (
                <div key={project.title} className="panel kinetic-border tilt-lift p-5">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="text-base font-black text-gray-950">{project.title}</h3>
                    <span className="px-2 py-1 rounded-lg border border-gray-200 bg-gray-50 text-xs font-semibold">{project.difficulty}</span>
                  </div>
                  <p className="text-sm text-gray-600 mt-2 leading-relaxed">{project.description}</p>
                  <div className="flex flex-wrap gap-2 mt-4">
                    {project.skills_covered.map((skill) => <SkillBadge key={skill} skill={skill} />)}
                  </div>
                  <div className="mt-4">
                    <div className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-2">Resume bullets</div>
                    <ul className="space-y-2">
                      {project.resume_bullets.map((bullet) => (
                        <li key={bullet} className="text-sm text-gray-700">- {bullet}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel kinetic-border p-5">
            <h2 className="text-2xl font-black text-slate-950 mb-4">Sample jobs used</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {data.sample_jobs.map((job) => (
                <a
                  key={`${job.source}-${job.url}-${job.title}`}
                  href={job.url || "#"}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-lg border border-white/80 bg-white/80 p-4 transition hover:-translate-y-1 hover:shadow-[0_18px_45px_rgba(15,23,42,0.1)]"
                >
                  <div className="text-sm font-bold text-gray-900">{job.title}</div>
                  <div className="text-xs text-gray-500 mt-1">{job.company || "Unknown company"} - {job.location || "Unknown location"}</div>
                  <div className="text-xs text-gray-400 mt-2">{job.source}</div>
                </a>
              ))}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
