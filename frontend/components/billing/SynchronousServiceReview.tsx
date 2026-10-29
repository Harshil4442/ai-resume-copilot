"use client";
import type { useQuotedSynchronousService } from "../../lib/useQuotedSynchronousService";
import { Button } from "../ui/Button";
import { ServiceChargeReview } from "./ServiceChargeReview";
export function SynchronousServiceReview<T>({ action }: { action: ReturnType<typeof useQuotedSynchronousService<T>> }) {
  return <div className="mt-4 space-y-3">
    {action.review && !action.recovery ? <ServiceChargeReview quote={action.review.quote} context={action.review.context}
      busy={action.pending} disabled={action.changed || !action.available || action.rejected}
      disabledReason={action.rejected ? "No request was started. Refresh this quote and review it again." : action.changed ? "The selected inputs changed. Cancel or refresh this review." : !action.available ? "This service is currently unavailable." : undefined}
      onAccept={action.approve} onRefresh={action.requestQuote} onCancel={action.cancel} /> : null}
    {action.recovery ? <div role="status" className="space-y-2 text-sm"><p>{action.pending ? "Processing the approved request…" : "This approved request needs a status check. No new request will be submitted."}</p><Button variant="secondary" disabled={action.pending} onClick={action.checkStatus}>Check approved request status</Button></div> : null}
    {action.error ? <p role="alert" className="text-sm text-coral">{action.error.message}</p> : null}
    {!action.recovery ? <Button variant="ghost" size="sm" disabled={action.pending || !action.canLookup} onClick={action.loadSaved}>Find saved result · no charge</Button> : null}
    {action.message ? <p role="status" className="text-sm text-muted-foreground">{action.message}</p> : null}
    {action.billing ? <p className="text-xs text-muted-foreground">{action.billing.reused ? `Saved result reused · 0 new credits charged (${action.billing.committed_credits} credits on its original delivery).` : `${action.billing.charged_this_request} credits charged for this delivery.`}</p> : null}
  </div>;
}
