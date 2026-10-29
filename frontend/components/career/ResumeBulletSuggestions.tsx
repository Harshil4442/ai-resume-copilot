"use client";
import type { CreditAvailability } from "../../lib/unifiedServiceQuotes";
import { bulletResult, parseResult } from "../../lib/synchronousResults";
import { useQuotedSynchronousService } from "../../lib/useQuotedSynchronousService";
import { SynchronousServiceReview } from "../billing/SynchronousServiceReview";
import { Button } from "../ui/Button";
const parse = parseResult(bulletResult);
export function ResumeBulletSuggestions({ resume, job, availability, approvedEvidence }: {
  resume: { id: number; filename: string }; job: { id: string; title: string; employer?: string; description: string };
  availability: CreditAvailability | null | undefined; approvedEvidence: boolean;
}) {
  const input = { resume_id: resume.id, job_description: job.description, tone: "concise" };
  const action = useQuotedSynchronousService({ operation: "bullet_rewrite", input, endpoint: "/llm/rewrite_bullets",
    selectionKey: JSON.stringify(input), context: { resume, job, details: [{ label: "Writing style", value: "Concise" }] }, availability, parse });
  return <section className="border-t border-border pt-7" aria-label="Resume bullet suggestions"><h2 className="font-display text-2xl">Improve individual resume bullets</h2>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">Review evidence-backed wording suggestions for this role. These suggestions do not replace text or change your original file.</p>
    <Button className="mt-4" variant="secondary" disabled={!approvedEvidence || !action.available || action.pending || Boolean(action.review || action.recovery)} onClick={action.requestQuote}>Review bullet suggestion charge</Button>
    {!approvedEvidence ? <p className="mt-2 text-xs text-muted-foreground">Approve supporting evidence for this resume first.</p> : !action.available ? <p className="mt-2 text-xs text-muted-foreground">This service is not currently available for credit execution.</p> : null}
    <SynchronousServiceReview action={action} />
    {action.result ? <div className="mt-5 space-y-4"><p className="text-sm">{action.result.summary}</p>{action.result.evidence_links.map((item, index) => <article className="rounded-lg border border-border p-4" key={index}><h3 className="text-sm font-semibold">Suggestion {index + 1}</h3><dl className="mt-3 space-y-3 text-sm"><div><dt className="font-semibold">Original</dt><dd className="mt-1 break-words text-muted-foreground">{item.original_text}</dd></div><div><dt className="font-semibold">Suggested wording</dt><dd className="mt-1 break-words">{item.replacement_text}</dd></div></dl><p className="mt-3 text-xs text-muted-foreground">Supported by {item.evidence_ids.length} approved evidence {item.evidence_ids.length === 1 ? "item" : "items"}.</p></article>)}</div> : null}
  </section>;
}
