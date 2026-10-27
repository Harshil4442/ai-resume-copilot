"use client";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { apiPostJson } from "../../lib/api";
import { answerResult, parseResult } from "../../lib/synchronousResults";
import type { CreditAvailability } from "../../lib/unifiedServiceQuotes";
import { useQuotedSynchronousService } from "../../lib/useQuotedSynchronousService";
import { SynchronousServiceReview } from "../billing/SynchronousServiceReview";
import { Button } from "../ui/Button";
const parse = parseResult(answerResult);
export function MatchQuestionPanel({ resume, job, matchId, availability }: {
  resume: { id: number; filename: string }; job: { id: string; title: string; employer?: string }; matchId: number;
  availability: CreditAvailability | null | undefined;
}) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"basic" | "enhanced">("basic");
  const input = { job_match_id: matchId, resume_id: resume.id, question: question.trim(), recent_messages: [], mode: "enhanced" };
  const key = JSON.stringify({ input, selectedMode: mode });
  const action = useQuotedSynchronousService({ operation: "enhanced_qa", input, endpoint: "/rag/ask", selectionKey: key,
    canLookup: question.trim().length >= 2, context: { resume, job, details: [{ label: "Question", value: question.trim() }] }, availability, parse,
    freeResult: (raw) => parse(raw)?.mode === "direct" });
  const direct = useMutation({
    mutationFn: async (selected: { key: string; input: typeof input; mode: typeof mode }) => {
      const result = parse(await apiPostJson<unknown>("/rag/ask", { ...selected.input, mode: "basic" }));
      if (!result) throw new Error("The stored-data answer could not be verified."); return result;
    },
    onSuccess: (result, selected) => { if (selected.key === key && selected.mode === "enhanced" && result.mode !== "direct") action.requestQuote(); }, retry: false,
  });
  const answer = action.result || (direct.variables?.key === key ? direct.data : null);
  return <section className="mt-10 border-t border-border pt-7" aria-label="Questions about this match"><h2 className="font-display text-2xl">Ask about this match</h2>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">Stored facts are checked first at no credit charge. Enhanced explanations require a separate review only when stored facts cannot answer the question.</p>
    <label className="mt-4 grid gap-2 text-sm font-semibold">Your question<textarea className="field-control min-h-24" value={question} minLength={2} maxLength={1000} disabled={direct.isPending || action.pending || Boolean(action.recovery)} onChange={(event) => setQuestion(event.target.value)} /></label>
    <label className="mt-3 grid gap-2 text-sm font-semibold">Answer mode<select className="field-control" value={mode} disabled={direct.isPending || action.pending || Boolean(action.recovery)} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="basic">Stored facts · no AI or credit charge</option><option value="enhanced" disabled={!action.available}>Enhanced explanation · review charge first</option></select></label>
    <Button className="mt-3" variant="secondary" disabled={question.trim().length < 2 || direct.isPending || action.pending || Boolean(action.review || action.recovery)} onClick={() => direct.mutate({ key, input, mode })}>{direct.isPending ? "Checking stored facts…" : "Ask question"}</Button>
    {direct.error ? <p role="alert" className="mt-3 text-sm text-coral">{direct.error.message}</p> : null}
    <SynchronousServiceReview action={action} />
    {answer ? <article className="mt-4 rounded-lg border border-border p-4 text-sm"><p className="text-xs font-semibold text-primary">{answer.mode === "direct" ? "Stored facts · 0 credits" : answer.mode === "enhanced" ? "Enhanced explanation" : "No supported stored-data answer"}</p><p className="mt-3 break-words whitespace-pre-wrap leading-7">{answer.answer}</p>{answer.sources.length ? <p className="mt-3 text-xs text-muted-foreground">Sources: {[...new Set(answer.sources.map((source) => source === "job" ? "Saved job description" : source === "match" ? "Saved match" : "Approved resume evidence"))].join(", ")}</p> : null}<p className="mt-2 text-xs text-muted-foreground">Confidence: {answer.confidence}</p></article> : null}
  </section>;
}
