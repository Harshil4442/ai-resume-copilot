"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useId, useState } from "react";

import { ApiError, apiGet } from "../../lib/api";
import type { AnalysisResult, Workspace } from "../../lib/career";
import { retryRead } from "../../lib/readQueryPolicy";
import { useWorkspacePage, workspaceKey } from "../../lib/workspacePages";
import { Button } from "../ui/Button";
import { StatusBadge } from "../ui/StatusBadge";
import { WorkspacePageControls } from "./WorkspaceHistory";

export type InterviewResult = {
  mode?: "curated" | "enhanced";
  opportunity_id: string;
  questions: {
    question: string;
    answer: string;
    answer_state?: "evidence_backed" | "evidence_needed";
    evidence_ids?: string[];
  }[];
};

export function InterviewQuestionList({ questions, reviewEvidence }: {
  questions: InterviewResult["questions"];
  reviewEvidence?: () => void;
}) {
  const id = useId();
  return <div className="mt-6 space-y-4">
    {questions.map((question, index) => {
      const evidenceBacked = question.answer_state === "evidence_backed";
      const sourceCount = question.evidence_ids?.length || 0;
      const heading = `${id}-question-${index}`;
      return (
        <article key={`${index}-${question.question}`} aria-labelledby={heading} className="rounded-xl border border-border bg-white p-5 transition-colors hover:border-accent sm:p-6">
          <div className="flex items-start gap-3 sm:gap-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center whitespace-nowrap rounded-full bg-surface font-mono text-sm font-medium tabular-nums text-primary" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            <div className="min-w-0 flex-1">
              <h3 id={heading} className="break-words text-base font-semibold leading-6 text-foreground sm:text-lg sm:leading-7">{question.question}</h3>
              <StatusBadge className="mt-2" tone={evidenceBacked ? "teal" : "amber"}>{evidenceBacked
                ? `${reviewEvidence ? "Evidence-backed" : "Linked at generation"} · ${sourceCount} ${sourceCount === 1 ? "source" : "sources"}`
                : "Evidence needed"}</StatusBadge>
            </div>
          </div>
          {question.answer ? (
            <div className="mt-5 rounded-lg bg-surface/80 p-4 sm:ml-14 sm:p-5">
              <p className="data-label">{evidenceBacked ? "Suggested talking points" : "Preparation guidance"}</p>
              <p className="mt-2 max-w-prose whitespace-pre-line break-words text-sm leading-7 text-muted-foreground">{question.answer}</p>
            </div>
          ) : null}
          {!evidenceBacked && reviewEvidence ? (
            <div className="mt-4 flex flex-col items-start gap-3 sm:ml-14 sm:flex-row sm:items-center sm:justify-between">
              <p className="max-w-prose text-xs leading-5 text-muted-foreground">Add or approve relevant evidence, then regenerate your questions.</p>
              <Button variant="secondary" size="sm" className="shrink-0" onClick={reviewEvidence}>Review resume evidence <ArrowRight size={14} aria-hidden="true" /></Button>
            </div>
          ) : null}
        </article>
      );
    })}
  </div>;
}

function resultUnavailable(error: unknown) {
  return error instanceof ApiError && [401, 403, 404, 410].includes(error.status);
}

function SavedInterview({ owner, opportunity, id }: { owner: string; opportunity: string; id: string }) {
  const result = useQuery({
    queryKey: [...workspaceKey(owner, opportunity), "saved-interview", id],
    queryFn: async ({ signal }) => {
      const response = await apiGet<AnalysisResult>(`/v1/analysis-runs/${encodeURIComponent(id)}/result`, signal);
      const payload = response.result as InterviewResult;
      if (response.id !== id || response.operation !== "interview_questions"
        || payload?.opportunity_id !== opportunity || !Array.isArray(payload.questions)) {
        throw new Error("The saved interview could not be verified.");
      }
      return payload;
    },
    retry: (count, error) => !resultUnavailable(error) && retryRead(count, error),
    staleTime: 0, gcTime: 0, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  if (result.isPending || result.isFetching) return <p role="status" className="mt-3 text-sm">Loading saved interview…</p>;
  if (result.isError) return <div role="alert" className="mt-3 space-y-2 text-sm">
    <p>{resultUnavailable(result.error) ? "This saved interview is no longer available." : "Could not load this saved interview."}</p>
    {!resultUnavailable(result.error) ? <Button variant="secondary" size="sm" onClick={() => void result.refetch()}>Retry saved interview</Button> : null}
  </div>;
  return <>
    <p className="mt-3 text-sm text-muted-foreground">These questions use the résumé and evidence available when they were generated.</p>
    <InterviewQuestionList questions={result.data.questions} />
  </>;
}

export function WorkspaceInterviewHistory({ owner, opportunity, seed, refreshing, refresh }: {
  owner: string;
  opportunity: string;
  seed: NonNullable<Workspace["pages"]["interview_results"]>;
  refreshing: boolean;
  refresh: () => unknown;
}) {
  const heading = useId();
  const [opened, setOpened] = useState<string | null>(null);
  const page = useWorkspacePage({ owner, opportunity, tab: "interview", source: null, collection: "interview_results",
    seed, enabled: true, refreshing, refresh });

  return <section aria-labelledby={heading} className="mt-8 border-t border-border pt-6">
    <h3 id={heading} className="font-display text-xl">Saved interviews</h3>
    {page.isLoading ? <p role="status" className="mt-3 text-sm">Loading saved interviews…</p> : null}
    {!page.items.length && !page.isLoading && !page.isError ? <p className="mt-3 text-sm text-muted-foreground">No saved interviews for this role.</p> : null}
    <ul className="mt-3 space-y-3">
      {page.items.map((item) => {
        const isOpen = opened === item.id;
        return <li key={item.id}>
          <Button size="sm" variant="secondary" className="h-auto min-h-9 max-w-full whitespace-normal text-left" aria-expanded={isOpen}
            onClick={() => setOpened(isOpen ? null : item.id)}>
            {isOpen ? "Close" : "Open"} saved interview from <time dateTime={item.completed_at}>{new Date(item.completed_at).toLocaleString("en-IN")}</time>
          </Button>
          {isOpen ? <SavedInterview owner={owner} opportunity={opportunity} id={item.id} /> : null}
        </li>;
      })}
    </ul>
    <WorkspacePageControls page={page} label="interviews" />
  </section>;
}
