"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPostJson } from "../../lib/api";

type ResumeItem = {
  id: number;
  filename: string;
  created_at: string;
};

type PartialMatch = {
  skill:    string;
  coverage: number;  // 0-100
  via:      string;
};

type MatchResponse = {
  match_id:        number;
  match_score:     number;
  required_skills: string[];
  full_matches:    string[];
  partial_matches: PartialMatch[];
  true_gaps:       string[];
  fit_summary:     string;
};

type HistoryItem = {
  match_id: number;
  job_title: string;
  company: string;
  match_score: number;
  created_at: string;
};

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 70 ? "text-green-600 bg-green-50 border-green-200"
    : score >= 40 ? "text-yellow-600 bg-yellow-50 border-yellow-200"
    : "text-red-600 bg-red-50 border-red-200";
  const label = score >= 70 ? "Strong Match" : score >= 40 ? "Moderate Match" : "Weak Match";
  return (
    <div className={`inline-flex flex-col items-center px-5 py-3 rounded-xl border-2 ${color}`}>
      <span className="text-3xl font-bold">{score.toFixed(1)}</span>
      <span className="text-xs font-semibold uppercase tracking-wide">{label}</span>
    </div>
  );
}

function SkillTag({ label, variant }: { label: string; variant: "required" | "missing" | "weak" }) {
  const styles = {
    required: "bg-blue-50 text-blue-700 border border-blue-200",
    missing:  "bg-red-50  text-red-700  border border-red-200",
    weak:     "bg-amber-50 text-amber-700 border border-amber-200",
  }[variant];
  return (
    <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${styles}`}>
      {label}
    </span>
  );
}

export default function JobsPage() {
  const [resumes, setResumes]           = useState<ResumeItem[]>([]);
  const [resumesLoading, setResumesLoading] = useState(true);
  const [selectedResumeId, setSelectedResumeId] = useState<string>("");
  const [jobTitle, setJobTitle]         = useState("Software Engineer");
  const [company, setCompany]           = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [data, setData]                 = useState<MatchResponse | null>(null);
  const [history, setHistory]           = useState<HistoryItem[]>([]);
  const [error, setError]               = useState<string | null>(null);
  const [resumeError, setResumeError]   = useState<string | null>(null);
  const [loading, setLoading]           = useState(false);
  const [showHistory, setShowHistory]   = useState(false);

  // Load user's parsed resumes for dropdown
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

  // Load match history
  useEffect(() => {
    apiGet<{ matches: HistoryItem[] }>("/jobs/matches")
      .then((res) => setHistory(res.matches))
      .catch(() => {});
  }, [data]); // refresh after each new match

  const canSubmit = useMemo(
    () => selectedResumeId && jobTitle.trim() && jobDescription.trim(),
    [selectedResumeId, jobTitle, jobDescription]
  );

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setData(null);
    setLoading(true);
    try {
      const res = await apiPostJson<MatchResponse>("/jobs/match", {
        resume_id: Number(selectedResumeId),
        job_title: jobTitle,
        company,
        job_description: jobDescription,
      });
      setData(res);
    } catch (err: any) {
      setError(err?.message || "Match failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="max-w-3xl mx-auto py-10 px-4 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Job Match</h1>
        <p className="text-sm text-gray-500 mt-1">
          Select your resume, paste a job description, and get an AI-powered fit analysis.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-4">
        {/* Resume dropdown */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Select Resume
          </label>
          {resumesLoading ? (
            <div className="w-full border rounded-lg px-3 py-2 text-sm text-gray-400 bg-gray-50">
              Loading resumes…
            </div>
          ) : resumeError ? (
            <div className="w-full border border-red-200 rounded-lg px-3 py-3 text-sm bg-red-50">
              <p className="text-red-600 font-medium">Failed to load resumes</p>
              <p className="text-red-500 text-xs mt-1">{resumeError}</p>
              <a href="/resume" className="text-blue-600 underline text-xs mt-2 inline-block">
                → Go to Resume page to upload and parse your resume
              </a>
            </div>
          ) : resumes.length === 0 ? (
            <div className="w-full border border-amber-200 rounded-lg px-3 py-3 text-sm bg-amber-50">
              <p className="text-amber-700 font-medium">No parsed resumes found</p>
              <p className="text-amber-600 text-xs mt-1">Upload and parse a resume before matching jobs.</p>
              <a href="/resume" className="text-blue-600 underline text-xs mt-2 inline-block">
                → Upload your resume here
              </a>
            </div>
          ) : (
            <select
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={selectedResumeId}
              onChange={(e) => setSelectedResumeId(e.target.value)}
            >
              {resumes.map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {r.filename || `Resume #${r.id}`} &nbsp;·&nbsp; ID: {r.id} &nbsp;·&nbsp;{" "}
                  {new Date(r.created_at).toLocaleDateString()}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Job title */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Job Title</label>
          <input
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="e.g. Senior Backend Engineer"
            value={jobTitle}
            onChange={(e) => setJobTitle(e.target.value)}
          />
        </div>

        {/* Company */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Company <span className="text-gray-400">(optional)</span>
          </label>
          <input
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="e.g. Acme Corp"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
        </div>

        {/* Job description */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Job Description</label>
          <textarea
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm min-h-[200px] focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Paste the full job description here…"
            value={jobDescription}
            onChange={(e) => setJobDescription(e.target.value)}
          />
        </div>

        <button
          type="submit"
          disabled={!canSubmit || loading}
          className="w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? "Analyzing match…" : "Analyze Match"}
        </button>
      </form>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {/* Match Results */}
      {data && (
        <div className="border border-gray-200 rounded-xl p-6 bg-white space-y-5 shadow-sm">
          <div className="flex items-center gap-4">
            <ScoreBadge score={data.match_score} />
            <div>
              <div className="text-sm font-semibold text-gray-900">
                {jobTitle}{company ? ` @ ${company}` : ""}
              </div>
              <div className="text-xs text-gray-400 mt-0.5">Match #{data.match_id}</div>
            </div>
          </div>

          {/* Fit summary */}
          {data.fit_summary && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                AI Fit Analysis
              </div>
              <p className="text-sm text-gray-700 leading-relaxed">{data.fit_summary}</p>
            </div>
          )}

          {/* Full matches */}
          {data.full_matches.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Full Matches ({data.full_matches.length})
              </div>
              <div className="flex flex-wrap gap-2">
                {data.full_matches.map((s) => (
                  <SkillTag key={s} label={s} variant="required" />
                ))}
              </div>
            </div>
          )}

          {/* Partial matches */}
          {data.partial_matches.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Partial Coverage ({data.partial_matches.length}) — you have related skills
              </div>
              <div className="flex flex-wrap gap-2">
                {data.partial_matches.map((p) => (
                  <span
                    key={p.skill}
                    title={`Your '${p.via}' covers ${p.coverage}% of '${p.skill}'`}
                    className="px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200 cursor-default"
                  >
                    {p.skill}{" "}
                    <span className="opacity-70">({p.coverage}% via {p.via})</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* True gaps */}
          {data.true_gaps.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                True Gaps ({data.true_gaps.length})
              </div>
              <div className="flex flex-wrap gap-2">
                {data.true_gaps.map((s) => (
                  <SkillTag key={s} label={s} variant="missing" />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Match History */}
      {history.length > 0 && (
        <div>
          <button
            onClick={() => setShowHistory((v) => !v)}
            className="text-sm text-blue-600 hover:underline font-medium"
          >
            {showHistory ? "Hide" : "Show"} match history ({history.length})
          </button>

          {showHistory && (
            <div className="mt-3 border border-gray-200 rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                  <tr>
                    <th className="px-4 py-2 text-left">Role</th>
                    <th className="px-4 py-2 text-left">Company</th>
                    <th className="px-4 py-2 text-right">Score</th>
                    <th className="px-4 py-2 text-right">Date</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {history.map((h) => (
                    <tr key={h.match_id} className="hover:bg-gray-50">
                      <td className="px-4 py-2 font-medium text-gray-800">{h.job_title}</td>
                      <td className="px-4 py-2 text-gray-500">{h.company || "—"}</td>
                      <td className="px-4 py-2 text-right">
                        <span
                          className={`font-semibold ${
                            h.match_score >= 70 ? "text-green-600"
                            : h.match_score >= 40 ? "text-yellow-600"
                            : "text-red-500"
                          }`}
                        >
                          {h.match_score.toFixed(1)}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right text-gray-400">
                        {new Date(h.created_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </main>
  );
}