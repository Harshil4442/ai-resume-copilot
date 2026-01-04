"use client";

import { useMemo, useState } from "react";
import { apiPostJson } from "../../lib/api";
import type { GapAnalysisResponse } from "../../lib/types";

const roles = ["Software Engineer", "Backend Engineer", "Frontend Engineer", "Data Scientist", "ML Engineer", "DevOps Engineer"];

export default function LearningPage() {
  const [target, setTarget] = useState(roles[0]);
  const [data, setData] = useState<GapAnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const currentSkills = useMemo(() => data?.current_skills ?? [], [data]);

  async function run() {
    setError(null);
    setLoading(true);
    try {
      const res = await apiPostJson<GapAnalysisResponse>("/recommendations/gaps", { target_role: target });
      setData(res);
    } catch (e: any) {
      setError(e.message || "Failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="max-w-3xl mx-auto py-10 space-y-6">
      <h1 className="text-2xl font-bold">Learning</h1>

      <div className="border rounded p-4 bg-white space-y-3">
        <div className="text-sm font-semibold">Target role</div>
        <select className="border rounded px-3 py-2 text-sm" value={target} onChange={(e) => setTarget(e.target.value)}>
          {roles.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>

        <button onClick={run} disabled={loading} className="px-4 py-2 rounded bg-black text-white text-sm disabled:opacity-60">
          {loading ? "Analyzing…" : "Find skill gaps from my latest resume"}
        </button>

        {error && <div className="text-sm text-red-600">{error}</div>}
      </div>

      {data && (
        <div className="space-y-6">
          <div className="border rounded p-4 bg-white space-y-2">
            <div className="text-sm font-semibold">Current skills (from latest resume)</div>
            <div className="flex flex-wrap gap-2">
              {currentSkills.map((s) => (
                <span key={s} className="px-2 py-1 rounded bg-gray-100 text-xs">{s}</span>
              ))}
              {!currentSkills.length && <span className="text-xs text-gray-500">No resume uploaded yet.</span>}
            </div>
          </div>

          <div className="border rounded p-4 bg-white space-y-2">
            <div className="text-sm font-semibold">Skill gaps</div>
            <div className="flex flex-wrap gap-2">
              {(data.skill_gaps as string[]).map((s: string) => (
                <span key={s} className="px-2 py-1 rounded bg-yellow-50 text-xs">{s}</span>
              ))}
              {!data.skill_gaps.length && <span className="text-xs text-gray-500">No gaps found.</span>}
            </div>
          </div>

          <div className="border rounded p-4 bg-white space-y-3">
            <div className="text-sm font-semibold">Recommended resources</div>
            <div className="space-y-3">
              {data.recommended_courses.map((c, idx) => (
                <div key={idx} className="border rounded p-3">
                  <div className="text-sm font-semibold">{c.title}</div>
                  <div className="text-xs text-gray-600">{c.platform} • {c.skill}</div>
                  <a className="text-sm underline" href={c.url} target="_blank" rel="noreferrer">Open</a>
                </div>
              ))}
              {!data.recommended_courses.length && (
                <div className="text-sm text-gray-500">No courses found for these gaps yet.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
