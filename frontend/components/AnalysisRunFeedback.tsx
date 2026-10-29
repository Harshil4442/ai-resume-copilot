import { CircleAlert, FilePlus2, LoaderCircle } from "lucide-react";
import Link from "next/link";

import type { AnalysisRun } from "../lib/career";
import { getTailoringGuidance } from "../lib/tailoringGuidance";
import { Button } from "./ui/Button";

export function AnalysisRunFeedback({ run, onSaveSourceSnapshot, sourceSnapshotDisabled = false }: {
  run: AnalysisRun | undefined;
  onSaveSourceSnapshot?: () => void;
  sourceSnapshotDisabled?: boolean;
}) {
  if (!run || run.status === "succeeded") return null;
  const currency = run.billing_currency === "credits" ? "credits" : run.billing_currency === "none" ? null : "analysis units";
  if (run.status === "failed") {
    const incompleteInterview = run.operation === "interview_questions" && run.error_code === "InterviewOutputError";
    const tailoring = run.operation === "resume_tailor";
    const guidance = tailoring ? getTailoringGuidance(run.failure_guidance) : null;
    const usageMessage = run.committed_units === 0 && run.usage_state === "released"
      ? currency ? `Any reserved ${currency} were released.` : "No credits were charged."
      : run.committed_units === 0 && run.usage_state === "waived"
        ? currency ? `No ${currency} were charged.` : "No credits were charged."
        : "";
    return (
      <div className="mt-4 flex gap-3 border-y border-coral/25 bg-coral/5 px-4 py-4 text-sm text-coral" role="alert">
        <CircleAlert size={18} className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <strong>{guidance?.title || (incompleteInterview ? "Could not generate a complete question set." : "Analysis did not complete.")}</strong>
          <p className="mt-1 leading-6 text-muted-foreground">{guidance?.nextStep || (incompleteInterview ? "Please try again." : "Try again later.")}</p>
          {usageMessage ? <p className="mt-1 text-muted-foreground">{usageMessage}</p> : null}
          {tailoring ? (
            <>
              <p className="mt-2 leading-6 text-muted-foreground">Your original resume is unchanged. Saving a source snapshot keeps that file as it is.</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                {onSaveSourceSnapshot ? <Button variant="secondary" size="sm" disabled={sourceSnapshotDisabled} onClick={onSaveSourceSnapshot}><FilePlus2 size={14} /> Save unchanged source snapshot</Button> : null}
                <Link href="/resume" className="rounded-md text-sm font-semibold text-primary underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary">Choose original or custom resume</Link>
              </div>
            </>
          ) : null}
        </div>
      </div>
    );
  }
  if (run.status === "cancelled") return <p className="mt-4 text-sm text-muted-foreground">Analysis cancelled.{run.usage_state === "released" ? ` Any reserved ${currency || "credits"} were released.` : ""}</p>;
  return (
    <div className="mt-4 flex items-center gap-3 border-y border-primary/20 bg-primary/5 px-4 py-4 text-sm text-foreground" role="status">
      <span className="animate-spin text-primary motion-reduce:animate-none" aria-hidden="true"><LoaderCircle size={18} /></span>
      <span>{run.status === "queued" ? "Analysis is queued" : "Analyzing the role against your approved resume evidence"}</span>
    </div>
  );
}
