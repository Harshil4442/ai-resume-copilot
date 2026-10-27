"use client";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { apiPostJson } from "../../lib/api";
import { learningResult, parseResult, safeResourceHref } from "../../lib/synchronousResults";
import type { CreditAvailability } from "../../lib/unifiedServiceQuotes";
import { useQuotedSynchronousService } from "../../lib/useQuotedSynchronousService";
import { SynchronousServiceReview } from "../billing/SynchronousServiceReview";
import { Button } from "../ui/Button";
const parse = parseResult(learningResult);
export function LearningStrategyPanel({ resume, job, matchId, availability }: {
  resume: { id: number; filename: string }; job: { id: string; title: string; employer?: string }; matchId: number;
  availability: CreditAvailability | null | undefined;
}) {
  const [mode, setMode] = useState<"curated" | "enhanced">("curated");
  const input = { match_id: matchId, mode: "enhanced" };
  const action = useQuotedSynchronousService({ operation: "learning_strategy", input, endpoint: "/recommendations/match_strategy", selectionKey: `${matchId}:${mode}`, context: { resume, job }, availability, parse });
  const allowance = availability?.free_core_limits.curated_learning;
  const curated = useMutation({ mutationFn: async (id: number) => { const result = parse(await apiPostJson<unknown>("/recommendations/match_strategy", { match_id: id, mode: "curated" })); if (!result) throw new Error("The curated plan could not be verified."); return result; }, retry: false });
  const result = action.result || (curated.variables === matchId ? curated.data : null);
  return <section className="mt-7 border-t border-border pt-7" aria-label="Learning plan for this role"><h2 className="font-display text-2xl">Build a learning plan for this role</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Suggested projects are future work. They do not become resume achievements until you complete them and approve the evidence.</p>
    <label className="mt-4 grid gap-2 text-sm font-semibold">Learning plan mode<select className="field-control" value={mode} disabled={action.pending || curated.isPending || Boolean(action.recovery)} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="curated" disabled={!allowance}>Curated resources · no generative AI</option><option value="enhanced" disabled={!action.available}>Personalized plan · review AI credit charge</option></select></label>
    {mode === "curated" ? <p className="mt-2 text-xs text-muted-foreground">{allowance ? `0 credits within an allowance of ${allowance.per_account_30_days} new plans per rolling 30 days, subject to available capacity.` : "Curated plans are not currently available for this account."}</p> : null}
    <Button className="mt-3" variant="secondary" disabled={action.pending || curated.isPending || Boolean(action.review || action.recovery) || (mode === "curated" ? !allowance : !action.available)} onClick={() => mode === "curated" ? curated.mutate(matchId) : action.requestQuote()}>{mode === "curated" ? "View curated plan" : "Review personalized plan charge"}</Button>
    {curated.error ? <p role="alert" className="mt-3 text-sm text-coral">{curated.error.message}</p> : null}
    <SynchronousServiceReview action={action} />
    {result ? <div className="mt-5 space-y-6 break-words text-sm"><p className="leading-7">{result.readiness_summary}</p>{result.warnings?.map((warning) => <p key={warning} className="text-muted-foreground">{warning}</p>)}
      <div><h3 className="font-semibold">Hiring signals to strengthen</h3><ul className="mt-3 space-y-2">{result.missing_hiring_signals.map((item, index) => <li key={index}><strong>{item.signal}</strong> — {item.why_it_matters}</li>)}</ul></div>
      <div><h3 className="font-semibold">Learning priorities</h3><div className="mt-3 space-y-4">{result.learning_priorities.map((item, index) => <article key={index} className="rounded-lg border border-border p-4"><h4 className="font-semibold">{item.skill} <span className="text-xs font-normal text-muted-foreground">· {item.priority} priority · {item.current_status}</span></h4><p className="mt-2">{item.reason}</p><p className="mt-2 text-muted-foreground">{item.expected_outcome}</p><ul className="mt-3 space-y-2">{item.resources.map((resource, i) => { const href = safeResourceHref(resource.url); return <li key={i}>{href ? <a className="font-semibold text-primary underline" href={href} target="_blank" rel="noreferrer">{resource.title}</a> : resource.title} · {resource.platform}</li>; })}</ul></article>)}</div></div>
      <div><h3 className="font-semibold">Practice projects</h3>{result.project_recommendations.map((project, index) => <article key={index} className="mt-3"><h4 className="font-semibold">{project.title}</h4><p className="mt-2">{project.description}</p><p className="mt-2 text-xs text-muted-foreground">Practice areas: {project.covers_gaps.join(", ")}</p><ol className="mt-2 list-decimal space-y-1 pl-5">{project.implementation_steps.map((step, i) => <li key={i}>{step}</li>)}</ol>{project.interview_talking_points.length ? <div className="mt-3"><p className="font-semibold">Discussion points after completing this work</p><ul className="mt-2 list-disc space-y-1 pl-5">{project.interview_talking_points.map((point, i) => <li key={i}>{point}</li>)}</ul></div> : null}</article>)}</div>
      <div><h3 className="font-semibold">Suggested sequence</h3><ol className="mt-3 space-y-3">{result.timeline.map((item, index) => <li key={index}><strong>{item.phase}: {item.focus}</strong><p className="mt-1 text-muted-foreground">{item.deliverable}</p></li>)}</ol></div>
    </div> : null}
  </section>;
}
