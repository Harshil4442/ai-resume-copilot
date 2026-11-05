"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { apiGet } from "../../lib/api";
import type { WorkspaceBody, WorkspaceVersion, ResumeVersion } from "../../lib/career";
import { retryRead } from "../../lib/readQueryPolicy";
import { workspaceKey } from "../../lib/workspacePages";
import { Button } from "../ui/Button";

type PageControls = { older: boolean; hasMore: boolean; isFetching: boolean; isError: boolean; more: () => void; newest: () => void; retry: () => void };
export function WorkspacePageControls({ page, label }: { page: PageControls; label: string }) {
  return <div role="group" className="mt-4 flex flex-wrap items-center gap-3" aria-label={`${label} pages`}>
    {page.isError ? <><p role="alert" className="text-sm text-coral">Could not load this page. Earlier records have not been removed.</p><Button size="sm" variant="secondary" onClick={page.retry} disabled={page.isFetching}>Retry {label}</Button></> : null}
    {page.older ? <Button size="sm" variant="ghost" onClick={page.newest} disabled={page.isFetching}>Newest {label}</Button> : null}
    {page.hasMore ? <Button size="sm" variant="secondary" onClick={page.more} disabled={page.isFetching}>{page.isFetching ? "Loading…" : `Older ${label}`}</Button> : null}
    {page.older || page.hasMore ? <p className="text-xs text-muted-foreground">Showing one page of up to 20 records.</p> : null}
  </div>;
}

/** Full bodies are read only when this exact owned row is opened. */
export function WorkspaceItemDetails<T extends { id: string }>({ owner, opportunity, source, collection, id, label, children }: {
  owner: string; opportunity: string; source?: number | null; collection: string; id: string;
  label: string; children: (item: T) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: [...workspaceKey(owner, opportunity), "detail", source, collection, id],
    queryFn: async ({ signal }) => {
      const item = await apiGet<T>(`/v1/opportunities/${encodeURIComponent(opportunity)}/workspace/${collection}/${encodeURIComponent(id)}`, signal);
      if (item.id !== id) throw new Error("The requested record could not be verified.");
      return item;
    },
    enabled: open, retry: retryRead, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  return <details onToggle={(event) => setOpen(event.currentTarget.open)} className="mt-3">
    <summary className="cursor-pointer text-sm font-semibold text-primary">{label}</summary>
    {open ? detail.isPending || detail.isFetching ? <p role="status" className="mt-3 text-sm">Loading exact record…</p> : detail.isError || !detail.data ? <div role="alert" className="mt-3 text-sm"><p>Could not load this record. No other record has been selected.</p><Button size="sm" variant="secondary" onClick={() => void detail.refetch()}>Retry record</Button></div> : children(detail.data) : null}
  </details>;
}

export function WorkspaceVersionDetails({ owner, opportunity, version, children }: {
  owner: string; opportunity: string; version: WorkspaceVersion; children: (item: ResumeVersion) => ReactNode;
}) {
  return <div className="rounded-lg border border-border p-4">
    <p className="break-words font-semibold">Version {version.version_number}: {version.label}</p>
    <p className="mt-1 text-xs text-muted-foreground">{version.approval_state}{version.submitted_at ? " · submitted" : ""}</p>
    <WorkspaceItemDetails<ResumeVersion> owner={owner} opportunity={opportunity} source={version.resume_id} collection="resume_versions" id={version.id} label={`Open version ${version.version_number} for review`}>{children}</WorkspaceItemDetails>
  </div>;
}

export function WorkspaceRoleBody({ owner, opportunity, outcome = false, label, children }: {
  owner: string; opportunity: string; outcome?: boolean; label?: string; children?: (body: WorkspaceBody) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const body = useQuery({
    queryKey: [...workspaceKey(owner, opportunity), "body"],
    queryFn: ({ signal }) => apiGet<WorkspaceBody>(`/v1/opportunities/${encodeURIComponent(opportunity)}/workspace/body`, signal),
    enabled: open, retry: retryRead, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  return <details className="mt-3" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-sm font-semibold text-primary">{label || (outcome ? "View saved outcome notes" : "View preserved role description")}</summary>
    {open ? body.isPending ? <p role="status">Loading saved text…</p> : body.isError || !body.data ? <div role="alert"><p>Saved text could not be loaded.</p><Button size="sm" variant="secondary" onClick={() => void body.refetch()}>Retry saved text</Button></div> : children ? children(body.data) : <p className="mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{(outcome ? body.data.outcome_notes : body.data.job_description) || "No saved text."}</p> : null}
  </details>;
}
