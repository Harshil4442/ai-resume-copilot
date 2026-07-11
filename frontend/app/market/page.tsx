"use client";

import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet, apiPostJson } from "../../lib/api";
import type { MarketAnalyzeResponse, MarketResumeGap, MarketTopSkill } from "../../lib/types";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { 
  Globe, Briefcase, MapPin, Navigation, Compass, AlertTriangle, 
  Target, BarChart3, TrendingUp, Zap, Sparkles, AlertCircle 
} from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

type ResumeItem = {
  id: number;
  filename: string;
  created_at: string;
};

function badgeClass(value: string) {
  const v = value.toLowerCase();
  if (v === "critical") return "bg-rose-900/30 text-rose-400 border-rose-800 shadow-sm";
  if (v === "high") return "bg-orange-50 text-orange-700 border-orange-200 shadow-sm";
  if (v === "medium") return "bg-amber-900/30 text-amber-400 border-amber-800 shadow-sm";
  if (v === "proven" || v === "high confidence") return "bg-emerald-900/30 text-emerald-400 border-emerald-800 shadow-sm";
  if (v === "claimed") return "bg-blue-900/30 text-blue-400 border-blue-700 shadow-sm";
  if (v === "missing" || v === "low confidence") return "bg-rose-900/30 text-rose-400 border-rose-800 shadow-sm";
  return "bg-slate-900/50 text-slate-200 border-slate-700 shadow-sm";
}

function Stat({ label, value, sub, icon: Icon }: { label: string; value: string; sub?: string; icon: any }) {
  return (
    <GlassCard className="p-5 flex flex-col justify-between h-full bg-slate-900/40" hoverEffect={false}>
      <div className="flex items-center gap-2 mb-3">
        <Icon size={16} className="text-primary" />
        <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold">{label}</div>
      </div>
      <div>
        <div className="text-2xl font-black text-white">{value}</div>
        {sub && <div className="text-xs text-slate-400 mt-1 font-medium">{sub}</div>}
      </div>
    </GlassCard>
  );
}

function SkillBadge({ skill }: { skill: string }) {
  return <span className="px-3 py-1 bg-slate-800 text-slate-200 rounded-full text-xs font-bold border border-slate-700 shadow-sm hover:scale-105 transition-transform">{skill}</span>;
}

function TopSkillsTable({ skills }: { skills: MarketTopSkill[] }) {
  return (
    <div className="overflow-x-auto custom-scrollbar">
      <table className="w-full text-sm">
        <thead className="text-[10px] uppercase tracking-wider text-slate-400 border-b border-slate-700 bg-slate-900/50">
          <tr>
            <th className="text-left py-3 px-4 font-bold">Skill</th>
            <th className="text-left py-3 px-4 font-bold">Category</th>
            <th className="text-right py-3 px-4 font-bold">Count</th>
            <th className="text-right py-3 px-4 font-bold">Demand</th>
            <th className="text-right py-3 px-4 font-bold">Importance</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {skills.map((skill) => (
            <tr key={skill.skill} className="hover:bg-slate-900/50 transition-colors">
              <td className="py-3 px-4 font-bold text-white">{skill.skill}</td>
              <td className="py-3 px-4 text-slate-400 font-medium">{skill.category}</td>
              <td className="py-3 px-4 text-right">{skill.count}</td>
              <td className="py-3 px-4 text-right font-black text-primary">{skill.percentage.toFixed(1)}%</td>
              <td className="py-3 px-4 text-right">
                <span className={twMerge(clsx("px-2.5 py-1 rounded-md border text-[10px] font-bold uppercase tracking-wider", badgeClass(skill.importance)))}>
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
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_120px_120px_1.6fr] gap-4 p-5 rounded-2xl bg-slate-950 border border-slate-700 shadow-sm hover:shadow-md transition-shadow">
      <div>
        <div className="text-sm font-black text-white">{gap.skill}</div>
        <div className="text-xs font-semibold text-slate-400 mt-1 flex items-center gap-1">
          <TrendingUp size={12} className="text-primary" /> Demand {gap.market_demand_percentage.toFixed(1)}%
        </div>
      </div>
      <div className="flex items-center lg:justify-center">
        <span className={twMerge(clsx("px-2.5 py-1 rounded-md border text-[10px] font-bold uppercase tracking-wider", badgeClass(gap.resume_status)))}>
          {gap.resume_status}
        </span>
      </div>
      <div className="flex items-center lg:justify-center">
        <span className={twMerge(clsx("px-2.5 py-1 rounded-md border text-[10px] font-bold uppercase tracking-wider", badgeClass(gap.priority)))}>
          {gap.priority} priority
        </span>
      </div>
      <p className="text-sm text-slate-300 leading-relaxed font-medium">{gap.reason}</p>
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
      window.dispatchEvent(new Event("refresh_analysis_units"));
    } catch (err: any) {
      setError(err?.message || "Market analysis failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="w-full max-w-[80rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader 
        badge="Market Intelligence"
        title="See which skills a sample of job postings is asking for."
        subtitle="Analyze recent postings from third-party job-data providers, extract frequently repeated skills, compare them against your resume, and pick projects that close high-value gaps. Results are informational estimates based on the sample analyzed."
      />

      <GlassCard className="p-0 overflow-hidden" hoverEffect={false}>
        <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] bg-slate-900/50">
          <div className="p-8 lg:p-10 flex flex-col justify-center">
            <h2 className="text-3xl font-black tracking-tighter text-white mb-4">Market Snapshot</h2>
            <p className="text-slate-300 leading-relaxed font-medium mb-10 max-w-lg">
              We analyze a sample of recent job postings from third-party job-data providers to estimate which skills are frequently requested for your target role.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-auto">
              <Stat label="Mode" value="On-demand" sub="Third-party job APIs" icon={Zap} />
              <Stat label="Extraction" value="LLM NLP" sub="Semantic deduplication" icon={Sparkles} />
              <Stat label="Coverage" value="Multi-region" sub="Subject to provider data" icon={Globe} />
            </div>
          </div>
          
          <div className="bg-slate-950 p-8 lg:p-10 border-t lg:border-t-0 lg:border-l border-slate-700">
            <form onSubmit={analyze} className="space-y-5">
              <div>
                <label className="block text-xs font-bold text-slate-200 mb-1">Target Role</label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400"><Briefcase size={16} /></div>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 pl-10 pr-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" value={targetRole} onChange={(e) => setTargetRole(e.target.value)} />
                </div>
              </div>
              
              <div className="grid grid-cols-1 sm:grid-cols-[2fr_1fr] gap-4">
                <div>
                  <label className="block text-xs font-bold text-slate-200 mb-1">Location</label>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400"><MapPin size={16} /></div>
                    <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 pl-10 pr-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" value={location} onChange={(e) => setLocation(e.target.value)} />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-200 mb-1">Country</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 uppercase" maxLength={2} value={countryCode} onChange={(e) => setCountryCode(e.target.value.toUpperCase())} />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-slate-200 mb-1">Experience</label>
                  <select className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" value={experienceLevel} onChange={(e) => setExperienceLevel(e.target.value)}>
                    <option value="">Any</option>
                    <option value="entry">Entry Level</option>
                    <option value="junior">Junior</option>
                    <option value="mid">Mid Level</option>
                    <option value="senior">Senior</option>
                    <option value="staff">Staff/Principal</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-200 mb-1">Work Mode</label>
                  <select className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" value={remote} onChange={(e) => setRemote(e.target.value)}>
                    <option value="any">Any Mode</option>
                    <option value="remote">Remote Only</option>
                    <option value="onsite">On-site or Hybrid</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-bold text-slate-200 mb-1">Compare Against Resume</label>
                <select className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" value={resumeId} onChange={(e) => setResumeId(e.target.value)}>
                  <option value="">No resume comparison (Market only)</option>
                  {resumes.map((resume) => (
                    <option key={resume.id} value={String(resume.id)}>
                      {resume.filename || `Resume #${resume.id}`} - {new Date(resume.created_at).toLocaleDateString()}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-slate-200 mb-1">Sample Size</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" type="number" min="5" max="100" value={maxResults} onChange={(e) => setMaxResults(Number(e.target.value))} />
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-200 mb-1">Posted Within (Days)</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" type="number" min="1" max="365" value={postedWithinDays} onChange={(e) => setPostedWithinDays(Number(e.target.value))} />
                </div>
              </div>

              <AnimatedButton 
                type="submit"
                disabled={loading || !targetRole.trim()} 
                className="w-full py-4 text-base mt-2 shadow-lg"
                showArrow
              >
                {loading ? "Analyzing market..." : "Analyze market demand (Free: 5 analysis units)"}
              </AnimatedButton>
            </form>
          </div>
        </div>
      </GlassCard>

      {error && (
        <FadeIn>
          <div className="text-sm text-rose-400 bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3 shadow-sm flex items-center gap-2 max-w-2xl mx-auto">
            <AlertCircle size={16} /> {error}
          </div>
        </FadeIn>
      )}

      {loading && (
        <FadeIn>
          <GlassCard className="p-16 text-center border-dashed border-slate-600 border-2" hoverEffect={false}>
            <div className="mx-auto w-12 h-12 rounded-full border-4 border-slate-700 border-t-primary animate-spin mb-6"></div>
            <h2 className="text-xl font-bold text-white tracking-tight">Analyzing the job market...</h2>
            <p className="text-sm text-slate-400 mt-2">Fetching recent postings from job-data providers, extracting skills with AI, and cross-referencing your resume.</p>
          </GlassCard>
        </FadeIn>
      )}

      {data && (
        <StaggerContainer className="space-y-8">
          <StaggerItem className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-6">
            <GlassCard className="p-6 md:p-8 bg-blue-900/30/50 border-blue-800" hoverEffect={false}>
              <div className="text-[10px] font-black uppercase tracking-widest text-blue-600 flex items-center gap-2 mb-4">
                <Sparkles size={14} /> AI Summary
              </div>
              <p className="text-xl font-bold text-white leading-relaxed tracking-tight">{data.summary}</p>
            </GlassCard>
            
            <GlassCard className="p-6 md:p-8 flex flex-col justify-center" hoverEffect={false}>
              <div className="grid grid-cols-2 gap-y-6 gap-x-4">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold mb-1">Sample Size</div>
                  <div className="text-3xl font-black text-white">{data.sample_size}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold mb-1">Confidence</div>
                  <div className="text-xl font-black text-white capitalize pt-1.5">{data.confidence}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold mb-1">Source</div>
                  <div className="text-sm font-bold text-slate-200">{data.source_provider}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold mb-1">Cache</div>
                  <div className="text-sm font-bold text-slate-200">{data.from_cache ? "Hit (Fast)" : "Live"}</div>
                </div>
              </div>
            </GlassCard>
          </StaggerItem>

          {data.warnings.length > 0 && (
            <StaggerItem>
              <GlassCard className="p-5 border-amber-800 bg-amber-900/30/80 backdrop-blur-sm" hoverEffect={false}>
                <div className="text-[10px] font-black uppercase tracking-wider text-amber-400 flex items-center gap-2 mb-2">
                  <AlertTriangle size={14} /> Analysis Warnings
                </div>
                <ul className="space-y-1 mt-3">
                  {data.warnings.map((warning) => (
                    <li key={warning} className="text-sm text-amber-800 font-medium flex gap-2">
                      <span className="text-amber-500">•</span> {warning}
                    </li>
                  ))}
                </ul>
              </GlassCard>
            </StaggerItem>
          )}

          <StaggerItem className="grid grid-cols-1 xl:grid-cols-[1.4fr_1fr] gap-6">
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <div className="mb-6">
                <h2 className="text-xl font-black text-white tracking-tighter">Top Demanded Skills</h2>
                <p className="text-sm text-slate-400 font-medium mt-1">Percentage indicates how many sampled postings explicitly required the skill.</p>
              </div>
              <div className="h-[360px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical" margin={{ left: 50, right: 20, top: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E2E8F0" />
                    <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fill: '#64748b', fontSize: 12, fontWeight: 600 }} axisLine={{ stroke: '#CBD5E1' }} />
                    <YAxis type="category" dataKey="skill" width={120} tick={{ fill: '#0f172a', fontSize: 12, fontWeight: 700 }} axisLine={false} tickLine={false} />
                    <Tooltip 
                      formatter={(value: number) => [`${Number(value).toFixed(1)}%`, "Demand"]}
                      contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1)', fontWeight: 'bold' }}
                    />
                    <Bar dataKey="demand" fill="#3b82f6" radius={[0, 6, 6, 0]} barSize={24} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>

            <GlassCard className="p-6 md:p-8 bg-slate-900/50" hoverEffect={false}>
              <h2 className="text-xl font-black text-white tracking-tighter mb-6">Category Breakdown</h2>
              <div className="space-y-6 max-h-[360px] overflow-y-auto pr-2 custom-scrollbar">
                {data.skill_categories.map((category) => (
                  <div key={category.category}>
                    <div className="text-[10px] font-black uppercase tracking-wider text-slate-400 mb-3">{category.category}</div>
                    <div className="flex flex-wrap gap-2">
                      {category.skills.slice(0, 8).map((skill) => (
                        <span key={skill.skill} className="px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-bold text-slate-200 shadow-sm flex items-center gap-2">
                          {skill.skill} <span className="text-[10px] font-black text-primary bg-blue-900/30 px-1.5 rounded">{skill.percentage.toFixed(0)}%</span>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="p-6 md:p-8 overflow-hidden" hoverEffect={false}>
              <h2 className="text-xl font-black text-white tracking-tighter mb-6">Complete Skill Demand Table</h2>
              <TopSkillsTable skills={data.top_skills.slice(0, 20)} />
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <div className="flex items-end justify-between gap-4 mb-6 px-1">
              <div>
                <h2 className="text-2xl font-black text-white tracking-tighter">Resume vs Market</h2>
                <p className="text-sm text-slate-400 font-medium mt-1">High-demand skills are classified as proven, claimed, or missing based on your parsed resume.</p>
              </div>
              <a href="/resume" className="text-sm font-bold text-primary hover:underline whitespace-nowrap">Update Resume &rarr;</a>
            </div>
            <div className="space-y-4">
              {data.resume_gap_analysis.length > 0 ? (
                data.resume_gap_analysis.slice(0, 12).map((gap) => <GapRow key={gap.skill} gap={gap} />)
              ) : (
                <GlassCard className="p-8 text-center text-sm font-medium text-slate-400 bg-slate-900/50 border-dashed" hoverEffect={false}>
                  Select a resume to compare in the form above to see the gap analysis.
                </GlassCard>
              )}
            </div>
          </StaggerItem>

          <StaggerItem>
            <h2 className="text-2xl font-black text-white tracking-tighter mb-6 px-1">Project ideas to close market gaps</h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {data.recommended_projects.map((project) => (
                <GlassCard key={project.title} className="p-6 md:p-8 h-full flex flex-col">
                  <div className="flex items-start justify-between gap-4 mb-4">
                    <h3 className="text-lg font-black text-white">{project.title}</h3>
                    <span className="px-2.5 py-1 rounded-md border border-slate-700 bg-slate-800 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap">{project.difficulty}</span>
                  </div>
                  <p className="text-sm text-slate-300 leading-relaxed font-medium flex-grow mb-6">{project.description}</p>
                  
                  <div className="mb-6">
                    <div className="text-[10px] font-black uppercase tracking-wider text-slate-400 mb-3">Skills Addressed</div>
                    <div className="flex flex-wrap gap-2">
                      {project.skills_covered.map((skill) => <SkillBadge key={skill} skill={skill} />)}
                    </div>
                  </div>
                  
                  <div className="pt-5 border-t border-slate-800 mt-auto">
                    <div className="text-[10px] font-black uppercase tracking-wider text-slate-400 mb-3 flex items-center gap-1.5"><FileText size={12} /> Target Resume Bullets</div>
                    <ul className="space-y-2.5">
                      {project.resume_bullets.map((bullet) => (
                        <li key={bullet} className="text-sm text-slate-200 font-medium leading-relaxed flex gap-2">
                          <span className="text-primary mt-1"><Target size={14} /></span>
                          {bullet}
                        </li>
                      ))}
                    </ul>
                  </div>
                </GlassCard>
              ))}
            </div>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <h2 className="text-xl font-black text-white tracking-tighter mb-6">Sample jobs used for analysis</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {data.sample_jobs.map((job) => (
                  <a
                    key={`${job.source}-${job.url}-${job.title}`}
                    href={job.url || "#"}
                    target="_blank"
                    rel="noreferrer"
                    className="group rounded-2xl border border-slate-700 bg-slate-900/50 p-5 transition-all hover:bg-slate-950 hover:border-primary/30 hover:shadow-lg flex flex-col"
                  >
                    <div className="text-sm font-black text-white group-hover:text-primary transition-colors line-clamp-2">{job.title}</div>
                    <div className="text-xs text-slate-400 font-medium mt-1 flex-grow">{job.company || "Unknown company"} • {job.location || "Unknown location"}</div>
                    <div className="text-[10px] font-bold text-slate-400 mt-4 uppercase tracking-wider flex justify-between items-center">
                      <span>{job.source}</span>
                      <ArrowUpRight size={14} className="opacity-0 group-hover:opacity-100 transition-opacity text-primary" />
                    </div>
                  </a>
                ))}
              </div>
            </GlassCard>
          </StaggerItem>
        </StaggerContainer>
      )}
    </main>
  );
}

// Re-using icon imported in dashboard but let's redefine just in case
import { ArrowUpRight, FileText } from "lucide-react";
