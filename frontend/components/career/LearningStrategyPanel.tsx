"use client";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { apiGet, apiPostJson } from "../../lib/api";
import { learningResult, parseResult, safeResourceHref } from "../../lib/synchronousResults";
import type { CreditAvailability } from "../../lib/unifiedServiceQuotes";
import { useQuotedSynchronousService } from "../../lib/useQuotedSynchronousService";
import { SynchronousServiceReview } from "../billing/SynchronousServiceReview";
import { Button } from "../ui/Button";
const parse = parseResult(learningResult);
type LearningStrategyProps = {
  resume: { id: number; filename: string }; job: { id: string; title: string; employer?: string }; matchId: number;
  availability: CreditAvailability | null | undefined;
  allowEnhanced?: boolean;
};
type LearningPlan = NonNullable<ReturnType<typeof parse>>;
export function LearningStrategyPanel(props: LearningStrategyProps) {
  // The enclosing session-owner boundary also remounts this private state on an account change.
  return <SelectedLearningStrategyPanel key={`${props.resume.id}:${props.job.id}:${props.matchId}:${props.allowEnhanced !== false}`} {...props} />;
}
function SelectedLearningStrategyPanel({ resume, job, matchId, availability, allowEnhanced = true }: LearningStrategyProps) {
  const [mode, setMode] = useState<"curated" | "enhanced">("curated");
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [saved, setSaved] = useState<{ stage: "idle" | "loading" | "missing" | "error" | "found"; result: LearningPlan | null }>({ stage: "idle", result: null });
  const scope = useRef(0);
  const lookup = useRef<AbortController | null>(null);
  useEffect(() => () => { lookup.current?.abort(); }, []);
  const input = { match_id: matchId, mode: "enhanced" };
  const action = useQuotedSynchronousService({ operation: "learning_strategy", input, endpoint: "/recommendations/match_strategy", selectionKey: `${matchId}:${mode}`, context: { resume, job }, availability, parse });
  const allowance = availability?.free_core_limits.curated_learning;
  const curated = useMutation({ mutationFn: async ({ id }: { id: number; scope: number }) => { const result = parse(await apiPostJson<unknown>("/recommendations/match_strategy", { match_id: id, mode: "curated" })); if (!result || result.match_id !== id || result.generated_by !== "curated" || result.provenance !== "curated") throw new Error("The curated plan could not be verified."); return result; }, retry: false });
  const currentCurated = curated.variables?.scope === selectionVersion;
  const result = mode === "enhanced" ? action.result : saved.result || (currentCurated ? curated.data : null);
  function changeMode(next: typeof mode) {
    scope.current += 1; lookup.current?.abort(); lookup.current = null;
    setSelectionVersion(scope.current); setSaved({ stage: "idle", result: null }); setMode(next);
  }
  async function findSaved() {
    if (lookup.current || mode !== "curated") return;
    const controller = new AbortController(), currentScope = scope.current;
    lookup.current = controller; curated.reset(); setSaved({ stage: "loading", result: null });
    try {
      const raw = await apiGet<unknown>(`/recommendations/match_strategy/saved?match_id=${matchId}`, controller.signal);
      if (controller.signal.aborted || currentScope !== scope.current) return;
      if (raw === null) { setSaved({ stage: "missing", result: null }); return; }
      const result = parse(raw);
      if (!result || result.match_id !== matchId || result.generated_by !== "curated" || result.provenance !== "curated") throw new Error("Invalid saved curated plan");
      setSaved({ stage: "found", result });
    } catch {
      if (!controller.signal.aborted && currentScope === scope.current) setSaved({ stage: "error", result: null });
    } finally { if (lookup.current === controller) lookup.current = null; }
  }
  return <section className="mt-7 border-t border-border pt-7" aria-label="Learning plan for this role"><h2 className="font-display text-2xl">Build a learning plan for this role</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Suggested projects are future work. They do not become resume achievements until you complete them and approve the evidence.</p>
    {allowEnhanced ? <label className="mt-4 grid gap-2 text-sm font-semibold">Learning plan mode<select className="field-control" value={mode} disabled={action.pending || curated.isPending || Boolean(action.recovery)} onChange={(event) => changeMode(event.target.value as typeof mode)}><option value="curated">Curated resources · no generative AI</option><option value="enhanced">Personalized plan · review AI credit charge</option></select></label> : null}
    {mode === "curated" ? <p className="mt-2 text-xs text-muted-foreground">{allowance ? `0 credits within an allowance of ${allowance.per_account_30_days} new plans per rolling 30 days, subject to available capacity.` : "New curated plans are currently paused. You can still look for an existing saved plan."}</p> : null}
    <Button className="mt-3" variant="secondary" disabled={action.pending || curated.isPending || saved.stage === "loading" || Boolean(action.review || action.recovery) || (mode === "curated" ? !allowance : !action.available)} onClick={() => { setSaved({ stage: "idle", result: null }); if (mode === "curated") curated.mutate({ id: matchId, scope: scope.current }); else action.requestQuote(); }}>{mode === "curated" ? "View curated plan" : "Review personalized plan charge"}</Button>
    {mode === "curated" ? <div className="mt-3 space-y-2"><Button variant="ghost" size="sm" disabled={saved.stage === "loading" || curated.isPending} onClick={() => void findSaved()}>Find saved curated plan</Button>
      {saved.stage === "loading" ? <p role="status" className="text-sm text-muted-foreground">Looking for your saved curated plan…</p> : null}
      {saved.stage === "missing" ? <p role="status" className="text-sm text-muted-foreground">No saved curated plan matches this role and its current inputs. No new plan was created.</p> : null}
      {saved.stage === "error" ? <p role="alert" className="text-sm text-coral">Could not verify your saved curated plan. Try the lookup again; no new plan was created.</p> : null}
      {saved.stage === "found" ? <p role="status" className="text-sm text-muted-foreground">Saved curated plan · no new credits charged.</p> : null}
    </div> : <SynchronousServiceReview action={action} />}
    {mode === "curated" && currentCurated && curated.error ? <p role="alert" className="mt-3 text-sm text-coral">{curated.error.message}</p> : null}
    {result ? <div className="mt-5 space-y-6 break-words text-sm"><p className="leading-7">{result.readiness_summary}</p>{result.warnings?.map((warning) => <p key={warning} className="text-muted-foreground">{warning}</p>)}
      <div><h3 className="font-semibold">Hiring signals to strengthen</h3><ul className="mt-3 space-y-2">{result.missing_hiring_signals.map((item, index) => <li key={index}><strong>{item.signal}</strong> — {item.why_it_matters}</li>)}</ul></div>
      <div><h3 className="font-semibold">Learning priorities</h3><div className="mt-3 space-y-4">{result.learning_priorities.map((item, index) => <article key={index} className="rounded-lg border border-border p-4"><h4 className="font-semibold">{item.skill} <span className="text-xs font-normal text-muted-foreground">· {item.priority} priority · {item.current_status}</span></h4><p className="mt-2">{item.reason}</p><p className="mt-2 text-muted-foreground">{item.expected_outcome}</p><ul className="mt-3 space-y-2">{item.resources.map((resource, i) => { const href = safeResourceHref(resource.url); return <li key={i}>{href ? <a className="font-semibold text-primary underline" href={href} target="_blank" rel="noreferrer">{resource.title}</a> : resource.title} · {resource.platform}</li>; })}</ul></article>)}</div></div>
      <div><h3 className="font-semibold">Practice projects</h3>{result.project_recommendations.map((project, index) => <article key={index} className="mt-3"><h4 className="font-semibold">{project.title}</h4><p className="mt-2">{project.description}</p><p className="mt-2 text-xs text-muted-foreground">Practice areas: {project.covers_gaps.join(", ")}</p><ol className="mt-2 list-decimal space-y-1 pl-5">{project.implementation_steps.map((step, i) => <li key={i}>{step}</li>)}</ol>{project.interview_talking_points.length ? <div className="mt-3"><p className="font-semibold">Discussion points after completing this work</p><ul className="mt-2 list-disc space-y-1 pl-5">{project.interview_talking_points.map((point, i) => <li key={i}>{point}</li>)}</ul></div> : null}</article>)}</div>
      <div><h3 className="font-semibold">Suggested sequence</h3><ol className="mt-3 space-y-3">{result.timeline.map((item, index) => <li key={index}><strong>{item.phase}: {item.focus}</strong><p className="mt-1 text-muted-foreground">{item.deliverable}</p></li>)}</ol></div>
    </div> : null}
  </section>;
}
