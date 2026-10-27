"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { ServiceReviewContext } from "../components/billing/ServiceChargeReview";
import { ApiError, apiPostJson } from "./api";
import { isCreditOperationAvailable, parseUnifiedServiceQuote, type CreditAvailability, type UnifiedServiceQuote } from "./unifiedServiceQuotes";

export type SynchronousOperation = "resume_enrichment" | "bullet_rewrite" | "enhanced_qa" | "learning_strategy";
export type CreditExecution = { run_id: string; billing_currency: "credits"; committed_credits: number; charged_this_request: number; reused: boolean };
export function parseCreditExecution(value: unknown): CreditExecution | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  if (typeof data.run_id !== "string" || !/^[A-Za-z0-9_.:-]{1,120}$/.test(data.run_id) || data.billing_currency !== "credits"
    || typeof data.reused !== "boolean" || ![data.committed_credits, data.charged_this_request].every((n) => typeof n === "number" && Number.isSafeInteger(n) && n >= 0)
    || (data.reused ? data.charged_this_request !== 0 : data.charged_this_request !== data.committed_credits)) return null;
  return data as CreditExecution;
}
type Request = { operation: SynchronousOperation; input: Record<string, unknown> };
type Intent = { request: Request; endpoint: string; selectionKey: string; executionKey: string; context: ServiceReviewContext; quote: UnifiedServiceQuote };
function detail(error: unknown) {
  return error instanceof ApiError && error.detail && typeof error.detail === "object"
    ? (error.detail as { detail?: { code?: unknown; run_created?: unknown; credits_charged?: unknown } }).detail : null;
}
export function useQuotedSynchronousService<T>({ operation, input, endpoint, selectionKey, context, availability, parse, onResult, freeResult, canLookup = true }: {
  operation: SynchronousOperation; input: Record<string, unknown>; endpoint: string; selectionKey: string;
  context: Omit<ServiceReviewContext, "operation" | "inputFingerprint">; availability: CreditAvailability | null | undefined;
  canLookup?: boolean; parse: (raw: unknown) => T | null; onResult?: (result: T) => void; freeResult?: (raw: unknown) => boolean;
}) {
  const [review, setReview] = useState<Intent | null>(null);
  const [recovery, setRecovery] = useState<Intent | null>(null);
  const [saved, setSaved] = useState<{ value: T; billing: CreditExecution | null; key: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const available = isCreditOperationAvailable(availability, operation);
  function checked(raw: unknown, key: string) {
    const value = parse(raw), billing = raw && typeof raw === "object" ? parseCreditExecution((raw as { credit_execution?: unknown }).credit_execution) : null;
    if (!value || (!billing && !freeResult?.(raw))) throw new Error("The saved delivery could not be verified. Check the original request status.");
    return { value, billing, key };
  }
  function publish(result: { value: T; billing: CreditExecution | null; key: string }) {
    setSaved(result); setReview(null); setRecovery(null);
    if (result.key === selectionKey) onResult?.(result.value);
    else setMessage("The earlier approved request is saved. Return to its selected inputs to view the result.");
    window.dispatchEvent(new Event("refresh_unified_credits"));
  }
  const execute = useMutation({
    mutationFn: async (intent: Intent) => {
      const result = checked(await apiPostJson<unknown>(intent.endpoint, {
        ...intent.request.input, accepted_service_quote_id: intent.quote.quote_id, idempotency_key: intent.executionKey,
      }), intent.selectionKey);
      if (result.billing && !result.billing.reused && result.billing.charged_this_request !== intent.quote.credits) throw new Error("The delivery charge does not match the reviewed quote. Check the original request status.");
      return result;
    },
    onMutate: (intent) => setRecovery(intent),
    onSuccess: publish,
    onError: (error) => {
      const contract = detail(error);
      if (contract?.code === "synchronous_service_rejected" && contract.run_created === false) setRecovery(null);
      if (contract?.code === "synchronous_delivery_unavailable" && contract.run_created === true && contract.credits_charged === 0) {
        setRecovery(null); setReview(null); setMessage("The attempt did not produce a validated deliverable. No credits were charged. Review a fresh quote to try again.");
      }
    }, retry: false,
  });
  const requestQuote = useMutation({
    mutationFn: async (prepareQuote: boolean) => {
      const request = structuredClone({ operation, input }), selectedContext = structuredClone(context), key = selectionKey;
      const existing = await apiPostJson<{ reused_run: { id: string; operation: string; status: string } | null; result: unknown }>("/v1/credits/synchronous-reuse", request);
      if (!existing || !("reused_run" in existing) || !("result" in existing)) throw new Error("Could not verify saved-result reuse.");
      if (existing.reused_run) {
        if (existing.reused_run.operation !== `sync_${operation}` || existing.reused_run.status !== "succeeded") throw new Error("An earlier request still needs a status check.");
        const result = checked(existing.result, key);
        if (!result.billing || result.billing.run_id !== existing.reused_run.id || !result.billing.reused || result.billing.charged_this_request !== 0) throw new Error("The saved result's billing status could not be verified.");
        return { result };
      }
      if (!prepareQuote) return { missing: true };
      if (!available) throw new Error("This service is not available for credit execution yet.");
      const quote = parseUnifiedServiceQuote(await apiPostJson<unknown>("/v1/credits/synchronous-service-quotes", request, { "Idempotency-Key": crypto.randomUUID() }));
      if (!quote || quote.operation !== operation) throw new Error("The service quote could not be verified. No work was started.");
      return { review: { request, endpoint, selectionKey: key, executionKey: crypto.randomUUID(), quote,
        context: { ...selectedContext, operation, inputFingerprint: quote.input_fingerprint } } satisfies Intent };
    },
    onSuccess: (result) => { if (result.result) publish(result.result); else if (result.review) setReview(result.review); else setMessage("No saved result matches these inputs. No credits were charged."); }, retry: false,
  });
  const checkStatus = useMutation({
    mutationFn: async (intent: Intent) => {
      const response = await apiPostJson<{ reused_run: { id: string; operation: string; status: string; committed_units: number; usage_state: string } | null; result: unknown }>("/v1/credits/synchronous-reuse", intent.request, { "Idempotency-Key": intent.executionKey });
      if (!response || !("reused_run" in response)) throw new Error("The original request status could not be verified.");
      if (!response.reused_run) return { pending: true };
      const run = response.reused_run;
      if (run.operation !== `sync_${intent.request.operation}`) throw new Error("The original request does not match this service.");
      if (run.status === "succeeded") {
        const result = checked(response.result, intent.selectionKey);
        if (!result.billing || result.billing.run_id !== run.id || !result.billing.reused) throw new Error("The saved result could not be verified.");
        return { result };
      }
      if (["failed", "cancelled"].includes(run.status) && run.committed_units === 0 && ["released", "waived"].includes(run.usage_state)) return { released: true };
      return { pending: true };
    },
    onSuccess: (result) => {
      if (result.result) { execute.reset(); publish(result.result); }
      else if (result.released) { execute.reset(); setRecovery(null); setReview(null); setMessage("The previous attempt ended without a credit charge. You can review a new quote."); }
      else setMessage("The original request is still unresolved. Check its status again; no new work will be submitted.");
    }, retry: false,
  });
  const rejected = detail(execute.error)?.code === "synchronous_service_rejected" && detail(execute.error)?.run_created === false;
  return { available, canLookup, review, recovery, message, rejected, changed: review !== null && review.selectionKey !== selectionKey,
    result: saved?.key === selectionKey ? saved.value : null, billing: saved?.key === selectionKey ? saved.billing : null,
    pending: requestQuote.isPending || execute.isPending || checkStatus.isPending,
    error: requestQuote.error || execute.error || checkStatus.error,
    requestQuote: () => { if (!recovery) { execute.reset(); checkStatus.reset(); setReview(null); setMessage(null); requestQuote.mutate(true); } },
    loadSaved: () => { if (!recovery && canLookup) { execute.reset(); checkStatus.reset(); setReview(null); setMessage(null); requestQuote.mutate(false); } },
    approve: (quoteId: string) => { if (review && quoteId === review.quote.quote_id && review.selectionKey === selectionKey && available && !recovery && !rejected) execute.mutate(review); },
    checkStatus: () => { if (recovery) checkStatus.mutate(recovery); },
    cancel: () => { if (!recovery) { setReview(null); execute.reset(); requestQuote.reset(); } },
  };
}
