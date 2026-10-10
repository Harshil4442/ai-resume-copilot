"use client";

import Link from "next/link";
import { useState } from "react";
import { trackEvent } from "../lib/analytics";
import FadeIn from "./ui/FadeIn";

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
    try {
      const res = await fetch("/api/backend/public/optimize_bullet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bullet_text: bullet }),
      });
      if (!res.ok) {
        throw new Error(`Optimization failed (${res.status})`);
      }
      const data = await res.json();
      setResult(data);
      trackEvent("bullet_optimizer_used", {
        action_verb_score: data.action_verb_score,
        metrics_present: Boolean(data.metrics_present),
      });
    } catch (optimizationError: unknown) {
      setError(
        optimizationError instanceof Error
          ? optimizationError.message
          : "Failed to connect to parser service.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto my-4 bg-transparent">
      <div className="eyebrow flex items-center gap-3">
        <span className="status-dot bg-primary" /> Public Micro-Tool
      </div>
      <h2 className="font-display text-4xl md:text-5xl font-normal text-foreground mt-2">
        Resume Bullet Review
      </h2>
      <p className="text-sm text-muted-foreground leading-6 mt-3">
        Test one resume bullet. HireWiz uses local writing checks to review action verbs and verified metrics. No AI call or analysis units are used.
      </p>

      <form onSubmit={handleOptimize} className="mt-6 space-y-4">
        <textarea
          className="field-control min-h-[130px] resize-y"
          aria-label="Resume bullet to review"
          placeholder="e.g. Worked on database performance improvements and cooperated with front-end developers."
          value={bullet}
          onChange={(e) => setBullet(e.target.value)}
          maxLength={500}
        />
        <button
          type="submit"
          disabled={loading || !bullet.trim()}
          className="button-primary w-full md:w-auto"
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
        <FadeIn duration={0.35} className="mt-8 pt-6 border-t border-border space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="surface-panel p-4 flex items-center justify-between">
              <div>
                <div className="data-label">Action Verb Score</div>
                <div className="text-3xl font-normal text-foreground mt-1">{result.action_verb_score} / 100</div>
              </div>
              <span className={`h-4 w-4 rounded-full ${result.action_verb_score >= 80 ? 'bg-emerald-400' : result.action_verb_score >= 60 ? 'bg-amber-400' : 'bg-red-400'}`} />
            </div>

            <div className="surface-panel p-4 flex items-center justify-between">
              <div>
                <div className="data-label">Quantifiable Metrics</div>
                <div className="text-lg font-normal text-foreground mt-2">
                  {result.metrics_present ? "✅ Present" : "❌ Missing (STAR gap)"}
                </div>
              </div>
            </div>
          </div>

          <div className="surface-soft p-5">
            <div className="eyebrow">Your original bullet</div>
            <p className="mt-2 text-base font-medium text-foreground leading-relaxed italic">
              "{result.recommended_bullet}"
            </p>
          </div>

          <div className="bg-surface border border-border text-foreground p-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4 rounded-2xl mt-8">
            <div>
              <div className="eyebrow">Continue reviewing your resume</div>
              <p className="text-sm text-muted-foreground mt-1">
                Create an account to structure your resume, compare it with job-description text, and review suggestions. HireWiz does not guarantee employment outcomes.
              </p>
            </div>
            <div className="flex flex-col sm:flex-row gap-3">
            <Link
              href="/register"
              onClick={() => trackEvent("signup_cta_clicked", { source: "bullet_optimizer" })}
              className="button-primary whitespace-nowrap"
            >
              Sign Up For Free
            </Link>
            <Link
              href="/pricing"
              onClick={() => trackEvent("premium_cta_clicked", { source: "bullet_optimizer" })}
              className="button-secondary whitespace-nowrap"
            >
              View credit packs
            </Link>
            </div>
          </div>
        </FadeIn>
      )}
    </div>
  );
}
