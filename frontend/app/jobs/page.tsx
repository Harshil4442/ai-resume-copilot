"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPostJson } from "../../lib/api";

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

function classNames(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

function confidenceClass(value: string) {
  const v = value.toLowerCase();
  if (v === "high") return "bg-green-50 text-green-700 border-green-200";
  if (v === "low") return "bg-red-50 text-red-700 border-red-200";
  return "bg-amber-50 text-amber-700 border-amber-200";
}

function gradeColor(g: string) {
  if (g.startsWith("A")) return "bg-green-50 text-green-700 border-green-200";
  if (g.startsWith("B")) return "bg-blue-50 text-blue-700 border-blue-200";
  if (g.startsWith("C")) return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-red-50 text-red-700 border-red-200";
}

function scoreColor(score: number) {
  if (score >= 75) return "from-green-500 to-emerald-600";
  if (score >= 55) return "from-amber-400 to-yellow-600";
  if (score >= 35) return "from-orange-400 to-red-500";
  return "from-red-500 to-rose-700";
}

function SkillTag({ label, variant }: { label: string; variant: "match" | "gap" | "partial" | "neutral" }) {
  const cls = {
    match: "bg-green-50 text-green-700 border-green-200",
    gap: "bg-red-50 text-red-700 border-red-200",
    partial: "bg-amber-50 text-amber-700 border-amber-200",
    neutral: "bg-slate-50 text-slate-700 border-slate-200",
  }[variant];
  return <span className={`px-2.5 py-1 rounded-full text-xs font-medium border ${cls}`}>{label}</span>;
}

function ScoreRing({ score }: { score: number }) {
  return (
    <div className={classNames("w-24 h-24 rounded-2xl bg-gradient-to-br text-white shadow-sm flex flex-col items-center justify-center", scoreColor(score))}>
      <span className="text-3xl font-black leading-none">{score.toFixed(0)}</span>
      <span className="text-[11px] uppercase tracking-wide opacity-90 mt-1">score</span>
    </div>
  );
}

function DimBar({ dim }: { dim: DimensionScore }) {
  const barColor = dim.score >= 75 ? "bg-green-500" : dim.score >= 50 ? "bg-amber-400" : "bg-red-400";
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
      <div className="flex justify-between items-center gap-3 mb-2">
        <span className="text-xs font-semibold text-gray-800">{dim.name}</span>
        <span className="text-xs font-bold text-gray-700">{dim.score.toFixed(0)}/100</span>
      </div>
      <div className="w-full bg-white rounded-full h-2 overflow-hidden">
        <div className={`h-2 rounded-full ${barColor}`} style={{ width: `${Math.max(3, Math.min(100, dim.score))}%` }} />
      </div>
      <p className="text-xs text-gray-500 mt-2 leading-relaxed">{dim.feedback}</p>
    </div>
  );
}

function EmptyState({ title, body, href }: { title: string; body: string; href?: string }) {
  return (
    <div className="border border-amber-200 rounded-xl px-4 py-4 text-sm bg-amber-50">
      <p className="text-amber-800 font-semibold">{title}</p>
      <p className="text-amber-700 text-xs mt-1">{body}</p>
      {href && <a href={href} className="text-blue-600 underline text-xs mt-3 inline-block">Open resume page</a>}
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
    } catch (e: any) {
      setError(e?.message || "Ask AI failed");
    } finally {
      setLoading(false);
    }
  }

  const latestAssistant = [...messages].reverse().find((msg) => msg.role === "assistant");

  return (
    <section className="border border-blue-100 rounded-2xl bg-white shadow-sm overflow-hidden">
      <div className="bg-gradient-to-r from-blue-50 via-white to-emerald-50 px-5 py-4 border-b border-blue-100">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Ask AI about this match</h2>
            <p className="text-sm text-gray-500 mt-1">Answers are grounded in this resume, JD, score breakdown, and match analysis.</p>
          </div>
          {latestAssistant?.confidence && (
            <span className={`px-2.5 py-1 rounded-full border text-xs font-semibold ${confidenceClass(latestAssistant.confidence)}`}>
              {latestAssistant.confidence} confidence
            </span>
          )}
        </div>
      </div>

      <div className="p-5 space-y-4">
        {messages.length === 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {starterQuestions.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                className="text-left rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:border-blue-200 hover:bg-blue-50 transition"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {messages.length > 0 && (
          <div className="max-h-[460px] overflow-y-auto space-y-3 pr-1">
            {messages.map((msg, idx) => (
              <div key={idx} className={classNames("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
                <div className={classNames(
                  "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
                  msg.role === "user" ? "bg-blue-600 text-white" : "bg-gray-50 text-gray-800 border border-gray-200",
                )}>
                  <div className="whitespace-pre-wrap">{msg.content}</div>
                  {msg.role === "assistant" && msg.confidence && (
                    <div className="mt-3">
                      <span className={`px-2 py-0.5 rounded-full border text-[11px] font-semibold ${confidenceClass(msg.confidence)}`}>
                        {msg.confidence} confidence
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="rounded-2xl px-4 py-3 text-sm bg-gray-50 text-gray-500 border border-gray-200">Thinking through the match context...</div>
              </div>
            )}
          </div>
        )}

        {latestAssistant?.suggested_followups && latestAssistant.suggested_followups.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {latestAssistant.suggested_followups.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                className="rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs text-blue-700 hover:bg-blue-100"
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
          className="flex gap-2"
        >
          <input
            className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Ask about score, gaps, evidence, interview prep..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button
            type="submit"
            disabled={!input.trim() || loading}
            className="px-4 py-2 rounded-xl bg-gray-900 text-white text-sm font-semibold hover:bg-black disabled:opacity-50"
          >
            Ask
          </button>
        </form>
        {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}
      </div>
    </section>
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
    } catch (err: any) {
      setError(err?.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="max-w-6xl mx-auto px-4 py-10 space-y-8">
      <section className="rounded-3xl bg-gradient-to-br from-slate-950 via-blue-950 to-slate-900 text-white p-6 md:p-8 shadow-sm">
        <div className="max-w-3xl">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-200">Job intelligence</div>
          <h1 className="text-3xl md:text-4xl font-black mt-3">Match a resume, then interrogate the result.</h1>
          <p className="text-sm md:text-base text-blue-100 mt-3 leading-relaxed">
            Run a multidimensional match analysis and ask follow-up questions grounded in the resume, job description, gaps, and recruiter scoring.
          </p>
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-[420px_1fr] gap-6 items-start">
        <form onSubmit={handleSubmit} className="space-y-5 bg-white border border-gray-200 rounded-2xl p-5 shadow-sm lg:sticky lg:top-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Analyze a target job</h2>
            <p className="text-sm text-gray-500 mt-1">Choose a parsed resume and paste the JD.</p>
          </div>

          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-1">Resume</label>
            {resumesLoading ? (
              <div className="w-full border rounded-xl px-3 py-2 text-sm text-gray-400 bg-gray-50">Loading resumes...</div>
            ) : resumeError ? (
              <div className="w-full border border-red-200 rounded-xl px-3 py-3 text-sm bg-red-50">
                <p className="text-red-600 font-medium">Failed to load resumes</p>
                <p className="text-red-500 text-xs mt-1">{resumeError}</p>
              </div>
            ) : resumes.length === 0 ? (
              <EmptyState title="No parsed resumes found" body="Upload and parse a resume first." href="/resume" />
            ) : (
              <select
                className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
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

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-1 gap-4">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-1">Job Title</label>
              <input
                className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Senior Backend Engineer"
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-1">Company <span className="text-gray-400">(optional)</span></label>
              <input
                className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Acme Corp"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-1">Job Description</label>
            <textarea
              className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm min-h-[260px] focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Paste the full job description here..."
              value={jobDescription}
              onChange={(e) => setJobDescription(e.target.value)}
            />
          </div>

          <button
            type="submit"
            disabled={!canSubmit || loading}
            className="w-full py-3 rounded-xl bg-blue-600 text-white text-sm font-bold hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            {loading ? "Analyzing match..." : "Analyze Match"}
          </button>

          {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3">{error}</div>}
        </form>

        <div className="space-y-6">
          {!data && (
            <section className="border border-dashed border-gray-300 rounded-2xl bg-white p-8 text-center">
              <div className="mx-auto w-14 h-14 rounded-2xl bg-blue-50 text-blue-700 flex items-center justify-center font-black text-xl">AI</div>
              <h2 className="text-xl font-bold text-gray-900 mt-4">Your match analysis will appear here</h2>
              <p className="text-sm text-gray-500 mt-2 max-w-md mx-auto">
                After the score is generated, you can ask AI questions about gaps, proof, score reasoning, and interview prep.
              </p>
            </section>
          )}

          {data && (
            <>
              <section className="border border-gray-200 rounded-2xl p-5 bg-white shadow-sm space-y-6">
                <div className="flex items-start gap-5 flex-wrap">
                  <ScoreRing score={data.match_score} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h2 className="text-xl font-black text-gray-900">
                        {jobTitle}{company ? ` @ ${company}` : ""}
                      </h2>
                      <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${gradeColor(data.grade)}`}>Grade {data.grade}</span>
                    </div>
                    <div className="text-xs text-gray-400 mt-1">Match #{data.match_id}</div>
                    <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2">
                        <div className="text-xs text-gray-500">Required skills</div>
                        <div className="text-lg font-bold">{data.required_skills.length}</div>
                      </div>
                      <div className="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2">
                        <div className="text-xs text-gray-500">True gaps</div>
                        <div className="text-lg font-bold">{data.true_gaps.length}</div>
                      </div>
                      <div className="rounded-xl bg-gray-50 border border-gray-100 px-3 py-2">
                        <div className="text-xs text-gray-500">Skill verification</div>
                        <div className="text-lg font-bold">{data.skill_verification_rate}%</div>
                      </div>
                    </div>
                  </div>
                </div>

                {data.fit_summary && (
                  <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
                    <div className="text-xs font-semibold text-blue-600 uppercase tracking-wide mb-1">AI Fit Analysis</div>
                    <p className="text-sm text-gray-700 leading-relaxed">{data.fit_summary}</p>
                  </div>
                )}

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                  <div>
                    <div className="text-xs font-bold text-gray-500 uppercase tracking-wide mb-3">Skill Coverage</div>
                    <div className="space-y-4">
                      <div>
                        <div className="text-xs font-semibold text-gray-700 mb-2">Full Matches ({data.full_matches.length})</div>
                        <div className="flex flex-wrap gap-2">
                          {data.full_matches.length ? data.full_matches.map((s) => <SkillTag key={s} label={s} variant="match" />) : <SkillTag label="No full matches yet" variant="neutral" />}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs font-semibold text-gray-700 mb-2">Partial Coverage ({data.partial_matches.length})</div>
                        <div className="flex flex-wrap gap-2">
                          {data.partial_matches.length ? data.partial_matches.map((p) => (
                            <SkillTag key={p.skill} label={`${p.skill} (${p.coverage}% via ${p.via})`} variant="partial" />
                          )) : <SkillTag label="No partial matches" variant="neutral" />}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs font-semibold text-gray-700 mb-2">True Gaps ({data.true_gaps.length})</div>
                        <div className="flex flex-wrap gap-2">
                          {data.true_gaps.length ? data.true_gaps.map((s) => <SkillTag key={s} label={s} variant="gap" />) : <SkillTag label="No true gaps found" variant="match" />}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div>
                    <div className="text-xs font-bold text-gray-500 uppercase tracking-wide mb-3">Recruiter Dimensions</div>
                    <div className="space-y-3">
                      {data.dimensions.map((dim) => <DimBar key={dim.name} dim={dim} />)}
                    </div>
                  </div>
                </div>

                {data.improvement_tips.length > 0 && (
                  <div className="bg-amber-50 border border-amber-100 rounded-xl px-4 py-4">
                    <div className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-2">How to Improve Your Match</div>
                    <ul className="space-y-1.5">
                      {data.improvement_tips.map((tip, i) => (
                        <li key={i} className="text-sm text-gray-700 flex gap-2">
                          <span className="text-amber-500 flex-shrink-0 font-bold">-</span>
                          {tip}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>

              <AskAiPanel match={data} resumeId={selectedResumeId} />
            </>
          )}

          {history.length > 0 && (
            <section className="border border-gray-200 rounded-2xl bg-white shadow-sm overflow-hidden">
              <button
                onClick={() => setShowHistory((v) => !v)}
                className="w-full px-5 py-4 text-left text-sm font-bold text-gray-900 hover:bg-gray-50 flex items-center justify-between"
              >
                <span>Match history ({history.length})</span>
                <span className="text-gray-400">{showHistory ? "Hide" : "Show"}</span>
              </button>
              {showHistory && (
                <div className="overflow-x-auto border-t border-gray-100">
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
                          <td className="px-4 py-2 text-gray-500">{h.company || "-"}</td>
                          <td className="px-4 py-2 text-right font-semibold">{h.match_score.toFixed(1)}</td>
                          <td className="px-4 py-2 text-right text-gray-400">{new Date(h.created_at).toLocaleDateString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </main>
  );
}
