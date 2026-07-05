"use client";

import { useState } from "react";

type OptimizationResult = {
  action_verb_score: number;
  metrics_present: boolean;
  recommended_bullet: string;
};

export default function BulletOptimizer() {
  const [bullet, setBullet] = useState("");
  const [result, setResult] = useState<OptimizationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleOptimize(e: React.FormEvent) {
    e.preventDefault();
    if (!bullet.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    // Call the un-gated public optimization endpoint
    const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";
    try {
      const res = await fetch(`${apiBase}/public/optimize_bullet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bullet_text: bullet }),
      });
      if (!res.ok) {
        throw new Error(`Optimization failed (${res.status})`);
      }
      const data = await res.json();
      setResult(data);
    } catch (err: any) {
      setError(err.message || "Failed to connect to parser service.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel kinetic-border p-6 md:p-8 max-w-4xl mx-auto my-12 bg-slate-900/70 backdrop-blur-xl">
      <div className="label-kicker flex items-center gap-3">
        <span className="pulse-dot bg-blue-900/300" /> Public Micro-Tool
      </div>
      <h2 className="text-3xl md:text-4xl font-black text-white mt-2">
        ATS Resume Bullet Optimizer
      </h2>
      <p className="text-sm text-slate-400 mt-2">
        Test a single resume accomplishment bullet. We'll score its action verbs and metrics density instantly.
      </p>

      <form onSubmit={handleOptimize} className="mt-6 space-y-4">
        <textarea
          className="w-full rounded-xl bg-slate-950/50 border border-slate-700/60 p-4 text-white placeholder-slate-500 outline-none focus:border-primary focus:ring-1 focus:ring-primary min-h-[100px]"
          placeholder="e.g. Worked on database performance improvements and cooperated with front-end developers."
          value={bullet}
          onChange={(e) => setBullet(e.target.value)}
          maxLength={500}
        />
        <button
          type="submit"
          disabled={loading || !bullet.trim()}
          className="w-full md:w-auto px-6 py-3 rounded-xl bg-primary text-white font-bold hover:bg-primary/90 transition shadow-sm"
        >
          {loading ? "Analyzing bullet..." : "Optimize Bullet"}
        </button>
      </form>

      {error && (
        <div className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-8 pt-6 border-t border-slate-700/60 space-y-6 animate-rise-fade">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="premium-card p-4 flex items-center justify-between">
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-slate-400">Action Verb Score</div>
                <div className="text-3xl font-black text-white mt-1">{result.action_verb_score} / 100</div>
              </div>
              <span className={`h-4 w-4 rounded-full ${result.action_verb_score >= 80 ? 'bg-emerald-400' : result.action_verb_score >= 60 ? 'bg-amber-400' : 'bg-red-400'}`} />
            </div>

            <div className="premium-card p-4 flex items-center justify-between">
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-slate-400">Quantifiable Metrics</div>
                <div className="text-lg font-black text-white mt-2">
                  {result.metrics_present ? "✅ Present" : "❌ Missing (STAR gap)"}
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-blue-800 bg-blue-900/30/40 p-5">
            <div className="text-xs font-bold uppercase tracking-wider text-blue-500">AI-Optimized Alternative</div>
            <p className="mt-2 text-base font-semibold text-white leading-relaxed italic">
              "{result.recommended_bullet}"
            </p>
          </div>

          <div className="bg-slate-950/60 border border-slate-800 text-white p-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4 rounded-xl shadow-lg mt-8">
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-blue-400">Ready to transform your career?</div>
              <p className="text-sm text-slate-300 mt-1">
                Upload your entire resume to automatically scan, match, and optimize for target roles.
              </p>
            </div>
            <a
              href="/register"
              className="bg-white text-slate-950 hover:bg-slate-200 transition text-center px-6 py-2.5 rounded-full font-black text-sm whitespace-nowrap"
            >
              Sign Up For Free
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
