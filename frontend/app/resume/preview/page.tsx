"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, FileUp, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LoadingBlock } from "../../../components/ui/LoadingBlock";
import { apiDownload, apiGet } from "../../../lib/api";
import { fetchResumeSource, fetchResumeVersionPdf } from "../../../lib/career";
import type { ResumeListResponse } from "../../../lib/types";

function ResumePreviewContent() {
  const searchParams = useSearchParams();
  const requestedId = Number(searchParams.get("resume"));
  const versionId = searchParams.get("version");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [pdfPreview, setPdfPreview] = useState<{ documentKey: string; url: string } | null>(null);
  const [pdfError, setPdfError] = useState<{ documentKey: string; message: string } | null>(null);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: () => apiGet<ResumeListResponse>("/resume/list") });
  const selectedResume = resumes.data?.resumes.find((resume) => resume.id === (selectedId ?? requestedId)) || resumes.data?.resumes[0];
  const resumeId = selectedResume?.id;
  const sourceAvailable = selectedResume?.source_available;
  const sourceFormat = selectedResume?.source_format;
  const documentKey = `${versionId ? requestedId : resumeId}:${versionId || "source"}:${previewAttempt}`;
  const sourceRequestId = versionId ? null : resumeId;
  const sourceRequestReady = !versionId && sourceAvailable && sourceFormat === "pdf";

  useEffect(() => {
    if (!versionId && (!sourceRequestId || !sourceRequestReady)) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const request = versionId ? fetchResumeVersionPdf(versionId, controller.signal) : fetchResumeSource(sourceRequestId!, controller.signal);
    void request
      .then((blob) => {
        if (controller.signal.aborted) return;
        if (blob.type.split(";")[0].trim().toLowerCase() !== "application/pdf") throw new Error("The server did not return a PDF file. Please try again.");
        objectUrl = URL.createObjectURL(blob);
        setPdfPreview({ documentKey, url: objectUrl });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setPdfError({ documentKey, message: error instanceof Error ? error.message : "Could not load this PDF." });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sourceRequestId, sourceRequestReady, versionId, documentKey]);

  const downloadOriginal = useMutation({ mutationFn: (resume: NonNullable<typeof selectedResume>) => apiDownload(`/resume/${resume.id}/source`, resume.filename) });
  const downloadTailored = useMutation({
    mutationFn: () => {
      if (!versionId) throw new Error("Choose a tailored version first.");
      return apiDownload(`/v1/resume-versions/${encodeURIComponent(versionId)}/download?format=pdf`, "hirewiz-tailored-resume.pdf");
    },
  });
  const pdfUrl = pdfPreview && pdfPreview.documentKey === documentKey ? pdfPreview.url : null;
  const previewError = pdfError && pdfError.documentKey === documentKey ? pdfError.message : null;
  function retryPreview() { setPdfError(null); setPdfPreview(null); setPreviewAttempt((attempt) => attempt + 1); }

  return (
    <main className="app-page">
      <div className="page-container max-w-5xl">
        <Link href={versionId ? "/workspace" : "/resume"} className="inline-flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground"><ArrowLeft size={16} /> {versionId ? "Workspace" : "Resume upload"}</Link>
        <header className="mt-5 border-b border-border pb-7">
          <p className="eyebrow">{versionId ? "Version review" : "Original source"}</p>
          <h1 className="font-display mt-2 text-4xl font-normal sm:text-5xl">{versionId ? "Tailored resume" : "Your original resume"}</h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">{versionId ? "This preview uses the exact PDF served by the download button. Check its text and layout, then return to your opportunity workspace to approve or reject the version." : "View or download the file you uploaded, with its original formatting. Tailored versions and proposed changes are reviewed in your opportunity workspace."}</p>
        </header>

        {!versionId && resumes.isLoading ? <div className="mt-8"><LoadingBlock rows={5} /></div> : null}
        {!versionId && resumes.isError ? <div className="mt-6 flex flex-wrap items-center gap-3" role="alert"><p className="text-sm text-coral">{resumes.error instanceof Error ? resumes.error.message : "Could not load your resumes."}</p><Button size="sm" variant="secondary" onClick={() => void resumes.refetch()}>Retry loading</Button></div> : null}
        {!versionId && !resumes.isLoading && !resumes.isError && !selectedResume ? <EmptyState icon={FileUp} title="Add your source resume" description="Upload a PDF or DOCX to keep the original file available for preview and tailoring." action={<Button asChild><Link href="/resume">Upload resume</Link></Button>} /> : null}

        {versionId ? (
          <section className="mt-7">
            {pdfUrl ? <Button variant="secondary" onClick={() => downloadTailored.mutate()} disabled={downloadTailored.isPending}><Download size={16} /> Download PDF</Button> : null}
            {downloadTailored.isError ? <p className="mt-3 text-sm text-coral" role="alert">{downloadTailored.error instanceof Error ? downloadTailored.error.message : "Could not download this version."}</p> : null}
            {previewError ? <div className="mt-6" role="alert"><p className="text-sm text-coral">Could not preview the tailored version. {previewError}</p><Button className="mt-3" size="sm" variant="secondary" onClick={retryPreview}>Retry preview</Button></div> : pdfUrl ? (
              <div className="mt-5 overflow-hidden rounded-lg border border-border bg-surface"><iframe src={pdfUrl} title="Tailored resume PDF" className="h-[75vh] min-h-[480px] w-full border-0" /><p className="px-4 py-3 text-xs leading-5 text-muted-foreground">If your browser cannot display this PDF, use Download PDF to review the same file.</p></div>
            ) : <div role="status"><p className="mb-4 text-sm text-muted-foreground">Loading tailored PDF...</p><LoadingBlock rows={6} /></div>}
          </section>
        ) : null}

        {selectedResume && !versionId ? (
          <section className="mt-7">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <label className="grid min-w-0 gap-2 text-sm font-semibold sm:max-w-lg sm:flex-1">
                Source resume
                <select className="field-control min-w-0 truncate" value={selectedResume.id} onChange={(event) => { setPdfPreview(null); setPdfError(null); setSelectedId(Number(event.target.value)); }}>
                  {resumes.data?.resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename}</option>)}
                </select>
              </label>
              {selectedResume.source_available && selectedResume.source_format ? <Button variant="secondary" onClick={() => downloadOriginal.mutate(selectedResume)} disabled={downloadOriginal.isPending}>{downloadOriginal.isPending ? <LoaderCircle size={16} className="animate-spin" /> : <Download size={16} />} Download original {selectedResume.source_format.toUpperCase()}</Button> : null}
            </div>
            {downloadOriginal.isError ? <p className="mt-3 text-sm text-coral" role="alert">{downloadOriginal.error instanceof Error ? downloadOriginal.error.message : "Could not download the original resume."}</p> : null}

            {!selectedResume.source_available || !selectedResume.source_format ? (
              <EmptyState icon={FileUp} title="Upload your source again" description="This older resume has extracted text, but its original file was not retained. Upload the PDF or DOCX again, then select the new resume in your opportunity workspace before tailoring." action={<Button asChild><Link href="/resume">Upload source again</Link></Button>} />
            ) : selectedResume.source_format === "tex" || selectedResume.source_format === "texzip" ? (
              <div className="surface-soft mt-7 p-6 sm:p-8"><h2 className="font-display text-2xl">Original TeX source is retained</h2><p className="mt-3 text-sm leading-6 text-muted-foreground">Download the unchanged source project to inspect its template. Prepare a source snapshot or tailored version in Workspace to review its sealed PDF before using it for an application. You can always choose your original or upload a custom PDF/DOCX.</p></div>
            ) : selectedResume.source_format === "docx" ? (
              <div className="surface-soft mt-7 p-6 sm:p-8">
                <FileText size={28} className="text-primary" aria-hidden="true" />
                <h2 className="font-display mt-4 text-2xl font-normal">Original DOCX is ready</h2>
                <p className="mt-3 max-w-prose text-sm leading-6 text-muted-foreground">Download the original file and open it in Word or another compatible document editor to view its layout. Your tailored version will also use DOCX.</p>
              </div>
            ) : previewError ? (
              <div className="mt-7 flex flex-wrap items-center gap-3" role="alert"><p className="text-sm text-coral">{previewError}</p><Button size="sm" variant="secondary" onClick={retryPreview}>Retry preview</Button></div>
            ) : pdfUrl ? (
              <div className="mt-7 overflow-hidden rounded-lg border border-border bg-surface">
                <iframe src={pdfUrl} title={`Original resume: ${selectedResume.filename}`} className="h-[75vh] min-h-[480px] w-full border-0" />
                <p className="px-4 py-3 text-xs leading-5 text-muted-foreground">If your browser cannot display this PDF, use Download original PDF to view it.</p>
              </div>
            ) : <div className="mt-7" role="status"><p className="mb-4 text-sm text-muted-foreground">Loading original PDF...</p><LoadingBlock rows={6} /></div>}
          </section>
        ) : null}
      </div>
    </main>
  );
}

export default function ResumePreviewPage() {
  return <Suspense fallback={<main className="app-page"><div className="page-container"><LoadingBlock rows={5} /></div></main>}><ResumePreviewContent /></Suspense>;
}
