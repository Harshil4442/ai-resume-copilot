"use client";

import { useId } from "react";
import { creditOperationLabel, serviceLimitLabel, type CreditOperation, type UnifiedServiceQuote } from "../../lib/unifiedServiceQuotes";
import { formatCreditDate } from "../../lib/unifiedCredits";
import { useServerClock } from "../../lib/useServerClock";
import { Button } from "../ui/Button";

export type ServiceReviewContext = {
  operation: CreditOperation;
  inputFingerprint: string;
  resume?: { id: string | number; filename: string };
  job?: { id: string; title: string; employer?: string };
  details?: { label: string; value: string }[];
};

type Props = {
  quote: UnifiedServiceQuote;
  context: ServiceReviewContext;
  onAccept: (quoteId: string) => void;
  onRefresh: () => void;
  onCancel: () => void;
  busy?: boolean;
  disabled?: boolean;
  disabledReason?: string;
};

/** The caller owns the selected records and checks unchanged-result reuse before requesting a quote. */
export function ServiceChargeReview({ quote, context, onAccept, onRefresh, onCancel, busy = false, disabled = false, disabledReason }: Props) {
  const id = useId(), now = useServerClock(quote.server_time, [Date.parse(quote.expires_at)]);
  const expired = now >= Date.parse(quote.expires_at);
  const changed = quote.operation !== context.operation || quote.input_fingerprint !== context.inputFingerprint;
  const hasContext = Boolean(context.resume?.filename.trim() || context.job?.title.trim() || context.details?.some((item) => item.value.trim()));
  return <section className="surface-panel space-y-5 p-5 sm:p-6" aria-labelledby={`${id}-heading`}>
    <div><p className="data-label">Review service charge</p><h2 id={`${id}-heading`} className="font-display mt-2 text-2xl">{creditOperationLabel(quote.operation)}</h2>
      <p className="mt-3 text-3xl font-semibold">{quote.credits} {quote.credits === 1 ? "credit" : "credits"}</p>
      <p className="mt-2 text-sm text-muted-foreground">{quote.mode === "enhanced" ? "Includes AI-assisted work" : "No generative AI call in this service"}</p></div>
    <dl className="space-y-3 text-sm leading-6">
      {context.resume ? <div><dt className="font-semibold">Resume</dt><dd className="break-words text-muted-foreground">{context.resume.filename}</dd></div> : null}
      {context.job ? <div><dt className="font-semibold">Job</dt><dd className="break-words text-muted-foreground">{context.job.title}{context.job.employer ? ` · ${context.job.employer}` : ""}</dd></div> : null}
      {context.details?.map((item, index) => <div key={`${item.label}-${index}`}><dt className="font-semibold">{item.label}</dt><dd className="break-words text-muted-foreground">{item.value}</dd></div>)}
      <div><dt className="font-semibold">What you receive</dt><dd className="text-muted-foreground">{quote.deliverable}</dd></div>
    </dl>
    {Object.keys(quote.supported_limits).length ? <div><h3 className="text-sm font-semibold">Included limits</h3><ul className="mt-2 space-y-2 text-sm text-muted-foreground">{Object.entries(quote.supported_limits).map(([key, value]) => <li key={key}>{serviceLimitLabel(key)}: {value.toLocaleString("en-IN")}</li>)}</ul></div> : null}
    <p className="text-xs leading-6 text-muted-foreground">Quote valid until <time dateTime={quote.expires_at}>{formatCreditDate(quote.expires_at)}</time> (Asia/Kolkata). This charge will not increase during the approved operation. Changed inputs need a fresh review.</p>
    <p className="text-xs leading-6 text-muted-foreground">{quote.operation === "job_discovery_batch" ? "A completed batch with 1–4 new verified jobs still costs one credit. An empty, failed or cancelled batch restores its reserved credit, usable for at least seven days after restoration. Each additional batch needs a separate review. " : "Failed or incomplete delivery restores credits usable for at least seven days after restoration, or receives the applicable monetary remedy. "}Viewing, downloading and unchanged reuse of an existing result do not trigger another generation charge.</p>
    {quote.operation === "verified_submission" ? <p className="text-sm leading-6">Employer submission also requires its separate review of the destination, exact file and answers.</p> : null}
    {changed ? <p role="alert" className="text-sm text-coral">The selected inputs changed. Get a new quote for the resume, job and choices you want to use.</p> : null}
    {expired ? <p role="alert" className="text-sm text-coral">This service quote expired. Refresh it and review the charge again.</p> : null}
    {!hasContext ? <p role="alert" className="text-sm text-coral">Select the file, job or service inputs before approving this charge.</p> : null}
    {disabled && disabledReason ? <p role="status" className="text-sm text-muted-foreground">{disabledReason}</p> : null}
    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
      <Button disabled={busy || disabled || expired || changed || !hasContext} onClick={() => onAccept(quote.quote_id)}>{busy ? "Starting approved work…" : `Approve ${quote.credits} ${quote.credits === 1 ? "credit" : "credits"}`}</Button>
      <Button variant="secondary" disabled={busy} onClick={onRefresh}>Refresh service quote</Button>
      <Button variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
    </div>
  </section>;
}
