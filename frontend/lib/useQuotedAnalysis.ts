"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiGet, apiPostJson } from "./api";
import type { AnalysisRun } from "./career";
import { isCreditOperationAvailable, parseUnifiedServiceQuote, type CreditAvailability, type CreditOperation, type UnifiedServiceQuote } from "./unifiedServiceQuotes";
import type { ServiceReviewContext } from "../components/billing/ServiceChargeReview";

export type QuotedAnalysisRequest = {
  operation: "job_match" | "interview_questions" | "resume_tailor" | "market_analysis";
  opportunity_id?: string;
  input: Record<string, unknown>;
};
type Review = { quote: UnifiedServiceQuote; request: QuotedAnalysisRequest; context: ServiceReviewContext; selectionKey: string; executionKey: string };
export function quotedCreditOperation(request: QuotedAnalysisRequest): CreditOperation {
  if (request.operation === "job_match") return "enhanced_job_match";
  if (request.operation === "market_analysis") return "market_report";
  if (request.operation === "resume_tailor" && request.input.tailoring_scope === "extended") return "resume_tailor_extended";
  return request.operation;
}

function definitelyRejected(error: unknown) {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.detail || typeof error.detail !== "object") return false;
  const detail = (error.detail as { detail?: { code?: unknown; run_created?: unknown } }).detail;
  return detail?.code === "credit_service_quote_rejected" && detail.run_created === false;
}

/** A quote never starts work. Execution retries keep the exact reviewed payload and identity. */
export function useQuotedAnalysis({ request, selectionKey, context, availability, onRun }: {
  request: QuotedAnalysisRequest;
  selectionKey: string;
  context: Omit<ServiceReviewContext, "operation" | "inputFingerprint">;
  availability: CreditAvailability | null | undefined;
  onRun: (run: AnalysisRun) => void;
}) {
  const [review, setReview] = useState<Review | null>(null);
  const [previousResult, setPreviousResult] = useState<string | null>(null);
  const operation = quotedCreditOperation(request);
  const available = isCreditOperationAvailable(availability, operation);
  const execution = useMutation({
    mutationFn: async (accepted: Review) => {
      try {
        return await apiPostJson<AnalysisRun>("/v1/analysis-runs", {
          ...accepted.request, accepted_service_quote_id: accepted.quote.quote_id,
        }, { "Idempotency-Key": accepted.executionKey });
      } catch (error) {
        const detail = error instanceof ApiError && error.status === 409 && error.detail && typeof error.detail === "object"
          ? (error.detail as { detail?: { code?: unknown; run_created?: unknown; run_id?: unknown } }).detail : null;
        if (detail?.code === "credit_service_quote_in_use" && detail.run_created === true && typeof detail.run_id === "string" && /^[A-Za-z0-9_.:-]{1,120}$/.test(detail.run_id)) {
          const run = await apiGet<AnalysisRun>(`/v1/analysis-runs/${encodeURIComponent(detail.run_id)}`);
          if (run.id !== detail.run_id || run.operation !== accepted.request.operation) throw new Error("The original approved request could not be verified.");
          return run;
        }
        throw error;
      }
    },
    onSuccess: (run, accepted) => { setReview(null); if (accepted.selectionKey === selectionKey) onRun(run); else setPreviousResult(`Your approved request for ${accepted.context.resume?.filename || accepted.context.job?.title || "the previous inputs"} was saved. Return to those inputs to view its result.`); window.dispatchEvent(new Event("refresh_unified_credits")); },
    retry: false,
  });
  const lookup = useMutation({
    mutationFn: async (prepareQuote: boolean) => {
      const selectedRequest = structuredClone(request), selectedContext = structuredClone(context), selectedKey = selectionKey;
      const reusable = await apiPostJson<{ reused_run: AnalysisRun | null }>("/v1/credits/analysis-reuse", selectedRequest);
      if (!reusable || !("reused_run" in reusable)) throw new Error("Could not verify whether an existing result can be reused.");
      if (reusable.reused_run) {
        const run = reusable.reused_run;
        if (typeof run.id !== "string" || !run.id || run.operation !== selectedRequest.operation || !["queued", "running", "succeeded"].includes(run.status)) throw new Error("The existing result could not be verified.");
        return { run, selectionKey: selectedKey };
      }
      if (!prepareQuote) return { missing: true };
      if (!available) throw new Error("This service is not available for credit execution yet.");
      const quote = parseUnifiedServiceQuote(await apiPostJson<unknown>("/v1/credits/service-quotes", {
        ...selectedRequest, idempotency_key: crypto.randomUUID(),
      }));
      if (!quote || quote.operation !== operation) throw new Error("The service quote could not be verified. No work was started.");
      return { review: { quote, request: selectedRequest, context: { ...selectedContext, operation, inputFingerprint: quote.input_fingerprint }, selectionKey: selectedKey, executionKey: crypto.randomUUID() } satisfies Review };
    },
    onSuccess: (result) => { if (result.run) {
      if (result.selectionKey === selectionKey) { onRun(result.run); setPreviousResult("Your existing request was loaded. No new credits were charged."); }
      else setPreviousResult("An existing result was found for the previous inputs. Return to those inputs to view it.");
    } else if (result.review) { execution.reset(); setReview(result.review); }
    else setPreviousResult("No saved result matches these inputs. No work was started or credits charged."); },
    retry: false,
  });
  const changed = review !== null && review.selectionKey !== selectionKey;
  const rejected = definitelyRejected(execution.error);
  const attempted = (execution.isError && !rejected) || execution.isPending;
  return {
    operation, available, review, changed, attempted, rejected, previousResult,
    pending: lookup.isPending || execution.isPending,
    error: lookup.error || execution.error,
    executionFailed: execution.isError && !rejected,
    requestQuote: () => { if (!attempted && !lookup.isPending) { setReview(null); setPreviousResult(null); execution.reset(); lookup.mutate(true); } },
    loadSaved: () => { if (!attempted && !lookup.isPending) { setReview(null); setPreviousResult(null); execution.reset(); lookup.mutate(false); } },
    approve: (quoteId: string) => { if (review && review.quote.quote_id === quoteId && !changed && available && !execution.isPending && !rejected) execution.mutate(review); },
    retry: () => { if (review && execution.isError) execution.mutate(review); },
    cancel: () => { if (!attempted) { setReview(null); lookup.reset(); execution.reset(); } },
  };
}
