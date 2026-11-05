"use client";

import { useState, type ReactNode, type Ref } from "react";

import { useResumeSelection, type ResumeItem, type ResumeOptions } from "../lib/resumeOptions";
import { Button } from "./ui/Button";

export function ResumeSelector({ catalog, value, onChange, label = "Resume", emptyLabel = "Choose a resume", disabled = false,
  required = false, retainedOnly = false, selectRef, format, onBrowse }: {
  catalog: ResumeOptions; value: string | number; onChange: (value: string) => void;
  label?: string; emptyLabel?: string; disabled?: boolean; required?: boolean; retainedOnly?: boolean;
  selectRef?: Ref<HTMLSelectElement>; format?: (resume: ResumeItem) => string; onBrowse?: () => void;
}) {
  const items = (catalog.data?.resumes ?? []).filter((item) => !retainedOnly || item.source_available);
  const missing = Boolean(value) && !items.some((item) => String(item.id) === String(value));
  return <div className="grid min-w-0 gap-2">
    <label className="grid min-w-0 gap-2 text-sm font-semibold">
      {label}
      <select ref={selectRef} className="field-control min-w-0 truncate" value={value} required={required}
        disabled={disabled || catalog.isLoading} onChange={(event) => onChange(event.target.value)}>
        <option value="">{emptyLabel}</option>
        {missing ? <option value={value} disabled>Selected resume · #{value}{catalog.isLoading ? " · loading" : " · unavailable"}</option> : null}
        {items.map((resume) => <option key={resume.id} value={resume.id}>{format ? format(resume) : `${resume.filename} · #${resume.id}`}</option>)}
      </select>
    </label>
    {catalog.isLoading ? <p role="status" className="text-xs text-muted-foreground">Loading saved resumes…</p> : null}
    {catalog.isError ? <div role="alert" className="space-y-2 text-xs"><p>{catalog.error instanceof Error ? catalog.error.message : "Could not load saved resumes."}</p><Button type="button" size="sm" variant="secondary" disabled={disabled || catalog.isFetching} onClick={catalog.refetch}>Retry resumes</Button></div> : null}
    {!catalog.isLoading && !catalog.isError && !items.length ? <p className="text-xs text-muted-foreground">{retainedOnly ? "No retained sources on this page." : "No saved resumes on this page."}</p> : null}
    {catalog.isOlderPage ? <Button type="button" size="sm" variant="secondary" disabled={disabled || catalog.isFetching} onClick={() => { onBrowse?.(); catalog.newest(); }}>Newest resumes</Button> : null}
    {catalog.hasMore ? <Button type="button" size="sm" variant="secondary" disabled={disabled || catalog.isFetching} onClick={() => { onBrowse?.(); catalog.loadMore(); }}>{catalog.isLoadingMore ? "Loading older resumes…" : "Load older resumes"}</Button> : null}
  </div>;
}

export type ResumeVersionSourceState = "not_requested" | "loading" | "error" | "unavailable" | "ready";

/** History can reference an older source: resolve it only when the owner asks. */
export function ResumeVersionMetadata({ resumeId, known, children, required = true }: {
  resumeId: number; known?: ResumeItem; required?: boolean; children: (resume: ResumeItem | undefined, state: ResumeVersionSourceState) => ReactNode;
}) {
  const [requested, setRequested] = useState(false);
  const metadata = useResumeSelection({ identity: resumeId, enabled: required && requested && !known });
  const value = known ?? (!metadata.isError ? metadata.data : undefined);
  const notFound = metadata.error !== null && typeof metadata.error === "object" && "status" in metadata.error && metadata.error.status === 404;
  const state: ResumeVersionSourceState = value ? value.source_available ? "ready" : "unavailable"
    : metadata.isFetching ? "loading"
    : requested && metadata.isError ? notFound ? "unavailable" : "error"
    : "not_requested";
  return <div className="space-y-2">
    {required && !known && (!metadata.data || metadata.isError) ? <div className="space-y-2 text-xs">
      <Button type="button" size="sm" variant="secondary" disabled={metadata.isFetching} onClick={() => { setRequested(true); if (requested) void metadata.refetch(); }}>{metadata.isFetching ? "Loading source details…" : "Load saved version source details"}</Button>
      {metadata.isError ? <p role="alert">Source details are unavailable. Retry without choosing another file.</p> : null}
    </div> : null}
    {children(value, state)}
  </div>;
}
