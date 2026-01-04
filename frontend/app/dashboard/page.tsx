"use client";

import { useEffect, useState } from "react";
import type { AnalyticsSummary } from "../../lib/types";
import { apiGet } from "../../lib/api";
import ScoreCard from "../../components/ScoreCard";
import MatchHistoryChart from "../../components/MatchHistoryChart";

export default function DashboardPage() {
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    apiGet<any>("/analytics/summary")
      .then((d) => mounted && setData(d as AnalyticsSummary))
      .catch((e) => mounted && setError(e?.message || "Failed to load dashboard"));
    return () => {
      mounted = false;
    };
  }, []);

  const avg =
    data && typeof (data as any).average_match_score === "number"
      ? (data as any).average_match_score
      : data && typeof (data as any).avg_match_score === "number"
      ? (data as any).avg_match_score
      : NaN;

  const avgText = Number.isFinite(avg) ? `${avg.toFixed(1)} / 100` : "—";

  return (
    <main className="max-w-4xl mx-auto py-10 space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {error && <div className="text-sm text-red-600">{error}</div>}
      {!data && !error && <div className="text-sm text-gray-600">Loading…</div>}

      {data && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <ScoreCard title="Profile completeness" value={`${data.profile_completeness ?? 0}%`} />
            <ScoreCard title="Average match score" value={avgText} />
            <ScoreCard title="Resumes parsed" value={`${data.resume_count ?? 0}`} />
          </div>

          <MatchHistoryChart data={data.match_history || []} />
        </>
      )}
    </main>
  );
}