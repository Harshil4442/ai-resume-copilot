import { LoaderCircle } from "lucide-react";

import { uploadPhaseLabels, type UploadProgress } from "../lib/resumeUpload";
import { Button } from "./ui/Button";

export function ResumeUploadProgress({ progress, onCancel }: { progress: UploadProgress; onCancel: () => void }) {
  return <div className="mt-3 rounded-lg border border-border bg-surface p-3">
    <p role="status" aria-live="polite" className="flex items-start gap-2 text-sm leading-6"><LoaderCircle aria-hidden size={16} className="mt-1 shrink-0 animate-spin motion-reduce:animate-none" />{uploadPhaseLabels[progress.phase]}</p>
    <div className="mt-2 flex flex-wrap items-center gap-3">
      <Button type="button" variant="secondary" size="sm" className="min-h-11" onClick={onCancel} disabled={progress.phase === "cancelling" || progress.phase === "completed"}>
        {progress.mode === "direct" ? "Cancel upload" : "Stop waiting"}
      </Button>
      <p className="max-w-lg text-xs leading-5 text-muted-foreground">{progress.mode === "direct"
        ? "Your original file stays private. Cancellation requests cleanup of this temporary upload; a file already saved may remain."
        : "This upload uses the current source route. Stopping waits does not undo a file that was already saved."}</p>
    </div>
  </div>;
}
