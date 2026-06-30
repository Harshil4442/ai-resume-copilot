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

function badgeClass(value: string) {
  const v = value.toLowerCase();
  if (v === "high") return "bg-red-50 text-red-700 border-red-200";
  if (v === "medium") return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-green-50 text-green-700 border-green-200";
}

function SignalCard({ signal }: { signal: MissingHiringSignal }) {
  return (
    <div className="panel kinetic-border tilt-lift p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-black text-slate-950">{signal.signal}</div>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full border ${badgeClass(signal.severity)}`}>
          {signal.severity}
        </span>
      </div>
      <p className="text-sm text-gray-600 mt-2 leading-relaxed">{signal.why_it_matters}</p>
    </div>
  );
}

function PriorityCard({ priority }: { priority: LearningPriority }) {
  return (
    <div className="panel kinetic-border tilt-lift p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-black text-slate-950">{priority.skill}</div>
          <div className="text-xs text-gray-500 mt-0.5">{priority.current_status.replaceAll("_", " ")}</div>
        </div>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full border ${badgeClass(priority.priority)}`}>
          {priority.priority}
        </span>
      </div>
      <p className="text-sm text-gray-600 leading-relaxed">{priority.reason}</p>
      <div className="text-xs text-gray-500">
        <span className="font-semibold text-gray-700">Outcome:</span> {priority.expected_outcome}
      </div>
      {priority.resources?.length > 0 && (
        <div className="space-y-2 pt-1">
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Useful resources</div>
          {priority.resources.map((r, idx) => (
            <a
              key={`${r.title}-${idx}`}
              href={r.url || "#"}
              target="_blank"
              rel="noreferrer"
              className="block border border-gray-100 rounded-md px-3 py-2 hover:bg-gray-50"
            >
              <div className="text-xs font-semibold text-gray-800">{r.title}</div>
              <div className="text-xs text-gray-500">
                {r.platform}{r.level ? ` · ${r.level}` : ""}
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function ProjectCard({ project }: { project: ProjectRecommendation }) {
  return (
    <div className="panel kinetic-border p-5 space-y-4">
      <div>
        <h2 className="text-xl font-black text-slate-950 ink-gradient">{project.title}</h2>
        <p className="text-sm text-gray-600 mt-1 leading-relaxed">{project.description}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        {project.covers_gaps.map((gap) => (
          <span key={gap} className="px-2 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-100 text-xs">
            {gap}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Build steps</div>
          <ul className="space-y-2">
            {project.implementation_steps.map((step, idx) => (
              <li key={`step-${idx}-${step.slice(0, 24)}`} className="text-sm text-gray-700 leading-snug">{step}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Resume bullets</div>
          <ul className="space-y-2">
            {project.resume_bullets.map((bullet, idx) => (
              <li key={`bullet-${idx}-${bullet.slice(0, 24)}`} className="text-sm text-gray-700 leading-snug">{bullet}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Interview talking points</div>
          <ul className="space-y-2">
            {project.interview_talking_points.map((point, idx) => (
              <li key={`point-${idx}-${point.slice(0, 24)}`} className="text-sm text-gray-700 leading-snug">{point}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
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
    // Mount-only fetch; `apiGet` is a stable module import.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    } catch (e: any) {
      setError(e?.message || "Failed to generate learning strategy");
    } finally {
      setLoadingStrategy(false);
    }
  }

  return (
    <main className="app-shell space-y-8">
      <section className="product-hero text-left p-7 md:p-10">
        <div className="label-kicker flex items-center gap-3"><span className="pulse-dot" />Learning Strategy</div>
        <h1 className="text-5xl md:text-7xl font-black leading-[0.88] mt-4 text-slate-950">Convert match gaps into portfolio proof.</h1>
        <p className="text-slate-600 mt-4 max-w-2xl leading-relaxed">
          Select a previous job match and generate a project-centered strategy with resume bullets and interview talking points.
        </p>
      </section>

      <section className="panel kinetic-border p-5 space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Job match</label>
          {loadingMatches ? (
            <div className="border rounded-lg px-3 py-2 text-sm text-gray-400 bg-gray-50">Loading matches...</div>
          ) : matches.length === 0 ? (
            <div className="border border-amber-200 rounded-lg px-3 py-3 text-sm bg-amber-50">
              <p className="text-amber-700 font-medium">No job matches found</p>
              <p className="text-amber-600 text-xs mt-1">Run a job match first, then return here for a learning strategy.</p>
              <a href="/jobs" className="text-blue-600 underline text-xs mt-2 inline-block">Go to Job Match Analyzer</a>
            </div>
          ) : (
            <select
              className="field"
              value={selectedMatchId}
              onChange={(e) => setSelectedMatchId(e.target.value)}
            >
              {matches.map((m) => (
                <option key={m.match_id} value={String(m.match_id)}>
                  {m.job_title}{m.company ? ` @ ${m.company}` : ""} · {m.match_score.toFixed(1)}/100 ·{" "}
                  {new Date(m.created_at).toLocaleDateString()}
                </option>
              ))}
            </select>
          )}
        </div>

        {selectedMatch && (
          <div className="flex flex-wrap gap-3 text-xs text-gray-500">
            <span>Match #{selectedMatch.match_id}</span>
            <span>Score: {selectedMatch.match_score.toFixed(1)}/100</span>
            <span>{new Date(selectedMatch.created_at).toLocaleDateString()}</span>
          </div>
        )}

        <button
          onClick={generateStrategy}
          disabled={!selectedMatchId || loadingStrategy || loadingMatches}
          className="btn-primary"
        >
          {loadingStrategy ? "Generating strategy..." : "Generate Learning Strategy"}
        </button>

        {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}
      </section>

      {strategy && (
        <div className="space-y-8">
          <section className="panel kinetic-border p-5 bg-blue-50/80 border-blue-100">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <div className="label-kicker">Readiness plan</div>
                <h2 className="text-2xl font-black text-slate-950 mt-1">
                  {strategy.job_title}{strategy.company ? ` @ ${strategy.company}` : ""}
                </h2>
              </div>
              <div className="text-right">
                <div className="text-xs text-gray-500">Current match</div>
                <div className="text-4xl font-black ink-gradient">{strategy.current_score.toFixed(1)}</div>
              </div>
            </div>
            <p className="text-sm text-gray-700 leading-relaxed mt-4">{strategy.readiness_summary}</p>
            {strategy.generated_by === "fallback" && (
              <div className="text-xs text-amber-700 mt-3">
                Showing fallback strategy because the LLM strategy generator was unavailable.
              </div>
            )}
          </section>

          <section>
            <h2 className="text-2xl font-black text-slate-950 mb-3">Missing Hiring Signals</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {strategy.missing_hiring_signals.map((signal, idx) => (
                <SignalCard key={`${signal.signal}-${idx}`} signal={signal} />
              ))}
            </div>
          </section>

          <section>
            <h2 className="text-2xl font-black text-slate-950 mb-3">Learning Priorities</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {strategy.learning_priorities.map((priority, idx) => (
                <PriorityCard key={`${priority.skill}-${idx}`} priority={priority} />
              ))}
            </div>
          </section>

          <section className="space-y-4">
            <h2 className="text-2xl font-black text-slate-950">Project Recommendations</h2>
            {strategy.project_recommendations.map((project, idx) => (
              <ProjectCard key={`${project.title}-${idx}`} project={project} />
            ))}
          </section>

          <section className="panel p-5">
            <h2 className="text-sm font-bold text-gray-900 mb-4">Suggested Timeline</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {strategy.timeline.map((item, idx) => (
                <div key={`${item.phase}-${idx}`} className="border border-gray-100 rounded-lg p-4 bg-gray-50">
                  <div className="text-xs font-semibold text-blue-600 uppercase tracking-wide">{item.phase}</div>
                  <div className="text-sm font-semibold text-gray-900 mt-1">{item.focus}</div>
                  <p className="text-xs text-gray-600 mt-2 leading-relaxed">{item.deliverable}</p>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
