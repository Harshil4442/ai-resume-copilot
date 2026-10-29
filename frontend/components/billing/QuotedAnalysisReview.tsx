"use client";

import type { useQuotedAnalysis } from "../../lib/useQuotedAnalysis";
import { creditOperationLabel } from "../../lib/unifiedServiceQuotes";
import { Button } from "../ui/Button";
import { ServiceChargeReview } from "./ServiceChargeReview";

export function QuotedAnalysisReview({ action }: { action: ReturnType<typeof useQuotedAnalysis> }) {
  return <div className="space-y-3">
    {action.previousResult ? <p role="status" className="text-sm">{action.previousResult}</p> : null}
    {action.review && !action.executionFailed ? <ServiceChargeReview quote={action.review.quote} context={action.review.context}
      busy={action.pending} disabled={action.changed || !action.available || action.rejected}
      disabledReason={action.rejected ? "This charge was rejected before work started. Refresh the quote and review it again." : action.changed ? "The selected inputs changed. Cancel this review and request a new quote." : !action.available ? "This service is currently unavailable." : undefined}
      onAccept={action.approve} onRefresh={action.requestQuote} onCancel={action.cancel} /> : null}
    {action.error ? <p role="alert" className="text-sm text-coral">{action.error.message}</p> : null}
    {action.executionFailed ? <div className="space-y-2 text-sm"><p>Approved charge: {action.review?.quote.credits} credits for {action.review?.context.resume?.filename || action.review?.context.job?.title || "the reviewed inputs"}. The request could not be confirmed. Retry checks the same approved request; it does not approve a second charge.</p><Button variant="secondary" onClick={action.retry}>Retry same approved request</Button></div> : null}
    {!action.attempted ? <Button variant="ghost" size="sm" disabled={action.pending} aria-label={`Find saved result for ${creditOperationLabel(action.operation)} · no charge`} onClick={action.loadSaved}>Find saved result · no charge</Button> : null}
  </div>;
}
