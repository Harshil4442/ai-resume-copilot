"use client";
import type { CreditAvailability } from "../../lib/unifiedServiceQuotes";
import { enrichmentResult, parseResult, type EnrichmentResult } from "../../lib/synchronousResults";
import { useQuotedSynchronousService } from "../../lib/useQuotedSynchronousService";
import { SynchronousServiceReview } from "../billing/SynchronousServiceReview";
import { Button } from "../ui/Button";
const parse = parseResult(enrichmentResult);
export function ResumeSkillEnrichment({ resume, availability, onResult }: { resume: { id: number; filename: string }; availability: CreditAvailability | null | undefined; onResult: (value: EnrichmentResult) => void }) {
  const action = useQuotedSynchronousService({ operation: "resume_enrichment", input: { resume_id: resume.id }, endpoint: `/resume/${resume.id}/enrich`,
    selectionKey: String(resume.id), context: { resume }, availability, parse, onResult });
  return <section className="mt-6 border-t border-border pt-5" aria-label="Optional resume skill enrichment">
    <h3 className="font-semibold">Optional skill enrichment</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Ask AI to identify additional skills explicitly supported by this saved resume. Your original file stays unchanged. Review the price before starting.</p>
    <Button className="mt-3" variant="secondary" disabled={!action.available || action.pending || Boolean(action.review || action.recovery)} onClick={action.requestQuote}>Review skill enrichment charge</Button>
    {!action.available ? <p className="mt-2 text-xs text-muted-foreground">Skill enrichment is not currently available for this account. The parsed resume remains usable.</p> : null}
    <SynchronousServiceReview action={action} />
  </section>;
}
