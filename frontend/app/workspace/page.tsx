"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BriefcaseBusiness,
  CalendarClock,
  MapPin,
  Plus,
  Search,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { apiPostJson } from "../../lib/api";
import {
  type Opportunity,
  stageLabels,
  stageTone,
  stages,
} from "../../lib/career";
import { trackEvent } from "../../lib/analytics";
import { opportunityPageOptions, type OpportunityPageItem } from "../../lib/opportunityPageClient";

import { useResumeOptions } from "../../lib/resumeOptions";
import { ResumeSelector } from "../../components/ResumeSelector";

const opportunitySchema = z.object({
  title: z.string().trim().min(2, "Add the role title").max(200),
  company: z.string().trim().max(200),
  location: z.string().trim().max(200),
  source_url: z.string().trim().max(2000),
  resume_id: z.string(),
  priority: z.enum(["low", "medium", "high"]),
  job_description: z.string().trim().min(20, "Paste enough of the role to preserve a useful snapshot").max(100_000),
});

type OpportunityForm = z.infer<typeof opportunitySchema>;

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(value));
}

function OpportunityRow({ opportunity }: { opportunity: OpportunityPageItem }) {
  return (
    <Link
      href={`/workspace/${encodeURIComponent(opportunity.id)}`}
      className="group grid min-h-28 gap-4 border-b border-border px-1 py-5 transition-colors hover:bg-surface sm:grid-cols-[1fr_auto] sm:items-center sm:px-4"
    >
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="font-display min-w-0 text-base font-normal text-foreground sm:text-lg">{opportunity.title}</h2>
          <StatusBadge tone={stageTone[opportunity.stage]}>{stageLabels[opportunity.stage]}</StatusBadge>
          {opportunity.priority === "high" ? <span className="text-xs font-bold text-primary">High priority</span> : null}
        </div>
        <p className="mt-1 text-sm font-semibold text-muted-foreground">{opportunity.company || "Company not set"}</p>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
          {opportunity.location ? <span className="flex items-center gap-1.5"><MapPin size={13} /> {opportunity.location}</span> : null}
          <span className="flex items-center gap-1.5"><CalendarClock size={13} /> Updated {formatDate(opportunity.updated_at)}</span>
          {opportunity.next_action ? <span>Next: {opportunity.next_action}</span> : null}
        </div>
      </div>
      <ArrowRight size={18} className="hidden text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-primary sm:block" aria-hidden="true" />
    </Link>
  );
}

function WorkspaceContent({ ownerKey, authenticated }: { ownerKey: string; authenticated: boolean }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const dialogTrigger = useRef<HTMLButtonElement | null>(null);
  const mounted = useRef(false);
  const [search, setSearch] = useState("");
  const [selection, setSelection] = useState({ stage: "all", search: "", includeArchived: false, cursor: null as string | null });
  const debouncing = search.trim() !== selection.search;
  const opportunities = useQuery(opportunityPageOptions({ ownerKey, stage: selection.stage === "all" ? null : selection.stage,
    search: selection.search, includeArchived: selection.includeArchived, cursor: selection.cursor,
    enabled: authenticated && !debouncing }));
  const page = !debouncing && !opportunities.isError ? opportunities.data : undefined;
  const loading = debouncing || (authenticated && opportunities.isPending);
  const form = useForm<OpportunityForm>({
    resolver: zodResolver(opportunitySchema),
    defaultValues: {
      title: "",
      company: "",
      location: "",
      source_url: "",
      resume_id: "",
      priority: "medium",
      job_description: "",
    },
  });
  const selectedResumeId = useWatch({ control: form.control, name: "resume_id" });
  const resumes = useResumeOptions({ selectedId: selectedResumeId, enabled: dialogOpen && authenticated, ownerKey });

  const newest = async () => {
    await queryClient.invalidateQueries({
      queryKey: opportunityPageOptions({ ownerKey, stage: selection.stage === "all" ? null : selection.stage,
        search: selection.search, includeArchived: selection.includeArchived }).queryKey,
      exact: true, refetchType: "none",
    });
    if (mounted.current) setSelection((current) => ({ ...current, cursor: null }));
  };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const timer = globalThis.setTimeout(() => setSelection((current) => current.search === search.trim()
      ? current : { ...current, search: search.trim(), cursor: null }), 300);
    return () => globalThis.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("new") !== "1") return;
    const timer = window.setTimeout(() => setDialogOpen(true), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const createOpportunity = useMutation({
    mutationFn: (values: OpportunityForm) =>
      apiPostJson<Opportunity>("/v1/opportunities", {
        ...values,
        source: values.source_url ? "url" : "manual",
        source_url: values.source_url || null,
        resume_id: values.resume_id ? Number(values.resume_id) : null,
      }),
    onSuccess: async (created) => {
      if (!mounted.current) return;
      trackEvent("opportunity_created", { source: created.source, has_resume: Boolean(created.resume_id) });
      setSelection((current) => ({ ...current, cursor: null }));
      await queryClient.invalidateQueries({ queryKey: ["opportunities"], refetchType: "none" });
      if (!mounted.current) return;
      setDialogOpen(false);
      form.reset();
      router.push(`/workspace/${created.id}`);
    },
  });

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="flex flex-col gap-6 border-b border-border pb-7 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="eyebrow">Career Workspace</p>
            <h1 className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Your opportunities</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              {page ? `${page.active_count} active ${page.active_count === 1 ? "role" : "roles"} and ${page.interview_count} in interview stage.`
                : opportunities.isError ? "Opportunity totals are unavailable." : "Loading your opportunity totals…"}
            </p>
          </div>
          <Button disabled={!authenticated} onClick={(event) => { dialogTrigger.current = event.currentTarget; setDialogOpen(true); }}>
            <Plus size={16} aria-hidden="true" /> Add opportunity
          </Button>
        </header>

        <section className="mt-6" aria-label="Opportunity filters">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex max-w-full gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Opportunity stages">
              {["all", ...stages].map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={selection.stage === value}
                  onClick={() => setSelection((current) => ({ ...current, stage: value, cursor: null }))}
                  className={`min-h-9 shrink-0 rounded-full px-3 text-xs font-medium transition-colors ${
                    selection.stage === value ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-surface hover:text-foreground"
                  }`}
                >
                  {value === "all" ? "All" : stageLabels[value]}
                </button>
              ))}
            </div>
            <label className="relative block w-full lg:w-72">
              <span className="sr-only">Search opportunities</span>
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                className="field-control pl-9"
                value={search}
                maxLength={200}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setSelection((current) => ({ ...current, cursor: null }));
                  void queryClient.cancelQueries({ queryKey: ["opportunities", "pages", ownerKey] });
                }}
                placeholder="Search role or company"
              />
            </label>
          </div>
          <label className="mt-3 inline-flex min-h-9 items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={selection.includeArchived}
              onChange={(event) => setSelection((current) => ({ ...current, includeArchived: event.target.checked, cursor: null }))} />
            Include archived opportunities
          </label>

          <div className="mt-5" aria-busy={loading}>
            {loading ? <LoadingBlock rows={5} /> : null}
            {!debouncing && opportunities.isError ? (
              <div role="alert" className="border-y border-coral/25 bg-coral/5 px-5 py-6 text-sm text-coral">
                {opportunities.error instanceof Error ? opportunities.error.message : "Could not load opportunities."}
                <Button variant="secondary" className="mt-3" onClick={() => { void opportunities.refetch(); }}>Refresh opportunities</Button>
                {selection.cursor ? <Button variant="secondary" className="ml-3 mt-3" onClick={() => { void newest(); }}>Return to newest opportunities</Button> : null}
              </div>
            ) : null}
            {page && page.items.length === 0 ? (
              <EmptyState
                icon={BriefcaseBusiness}
                title={page.total_owned ? "No opportunities match this page or these filters" : "Add your first target role"}
                description={page.total_owned ? "Change the stage or search, or return to the newest results." : "Preserve the role, connect your resume, and keep every decision in one place."}
                action={!page.total_owned ? <Button onClick={(event) => { dialogTrigger.current = event.currentTarget; setDialogOpen(true); }}><Plus size={16} /> Add opportunity</Button> : undefined}
              />
            ) : null}
            {page ? <>
              <p className="mb-3 text-xs text-muted-foreground" role="status">Showing {page.items.length} of {page.total} matching opportunities · {page.total_owned} saved in total.</p>
              {page.items.length ? <div className="border-t border-border">{page.items.map((item) => <OpportunityRow key={item.id} opportunity={item} />)}</div> : null}
              <nav aria-label="Opportunity pages" className="mt-5 flex flex-wrap items-center justify-between gap-3">
                <Button variant="secondary" disabled={!selection.cursor || opportunities.isFetching}
                  onClick={() => { void newest(); }}>Newest opportunities</Button>
                <span className="text-xs text-muted-foreground">{selection.cursor ? "Viewing older results" : "Viewing newest results"}</span>
                <Button variant="secondary" disabled={!page.next_cursor || opportunities.isFetching}
                  onClick={() => { const cursor = page.next_cursor; if (cursor) setSelection((current) => ({ ...current, cursor })); }}>Older opportunities</Button>
              </nav>
            </> : null}
          </div>
        </section>
      </div>

      <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[70] bg-foreground/25 backdrop-blur-sm" />
          <Dialog.Content
            className="fixed left-1/2 top-1/2 z-[71] max-h-[90vh] w-[min(94vw,680px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-border bg-card p-5 shadow-2xl sm:p-7"
            onCloseAutoFocus={(event) => {
              if (!dialogTrigger.current?.isConnected) return;
              event.preventDefault();
              dialogTrigger.current.focus();
            }}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <Dialog.Title className="text-xl font-semibold text-foreground">Add an opportunity</Dialog.Title>
                <Dialog.Description className="mt-1 text-sm text-muted-foreground">The original role snapshot is preserved when you create it.</Dialog.Description>
              </div>
              <Dialog.Close asChild><button type="button" className="icon-button" aria-label="Close"><X size={18} /></button></Dialog.Close>
            </div>

            <form className="mt-6 grid gap-5" onSubmit={form.handleSubmit((values) => createOpportunity.mutate(values))}>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="grid gap-2 text-sm font-semibold text-foreground">
                  Role title
                  <input className="field-control" {...form.register("title")} placeholder="Senior Product Designer" />
                  {form.formState.errors.title ? <span className="text-xs text-coral">{form.formState.errors.title.message}</span> : null}
                </label>
                <label className="grid gap-2 text-sm font-semibold text-foreground">
                  Company
                  <input className="field-control" {...form.register("company")} placeholder="Company name" />
                </label>
                <label className="grid gap-2 text-sm font-semibold text-foreground">
                  Location
                  <input className="field-control" {...form.register("location")} placeholder="Bengaluru or remote" />
                </label>
                <label className="grid gap-2 text-sm font-semibold text-foreground">
                  Priority
                  <select className="field-control" {...form.register("priority")}>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </label>
                <label className="grid gap-2 text-sm font-semibold text-foreground sm:col-span-2">
                  Source URL
                  <input className="field-control" {...form.register("source_url")} placeholder="https://company.example/careers/role" inputMode="url" />
                </label>
                <div className="sm:col-span-2"><ResumeSelector catalog={resumes} value={selectedResumeId} emptyLabel="Connect later" onChange={(value) => form.setValue("resume_id", value, { shouldValidate: true })} /></div>
              </div>
              <label className="grid gap-2 text-sm font-semibold text-foreground">
                Job description
                <textarea className="field-control min-h-52 resize-y" {...form.register("job_description")} placeholder="Paste the role description" />
                {form.formState.errors.job_description ? <span className="text-xs text-coral">{form.formState.errors.job_description.message}</span> : null}
              </label>
              {createOpportunity.isError ? <p className="text-sm text-coral">{createOpportunity.error instanceof Error ? createOpportunity.error.message : "Could not create opportunity."}</p> : null}
              <div className="flex flex-col-reverse gap-2 border-t border-border pt-5 sm:flex-row sm:justify-end">
                <Button type="button" variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
                <Button type="submit" disabled={createOpportunity.isPending}>{createOpportunity.isPending ? "Creating..." : "Create workspace"}</Button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </main>
  );
}

export default function WorkspacePage() {
  const { data: session, status } = useSession();
  const ownerKey = `${status}:${session?.user?.id || ""}`;
  return <WorkspaceContent key={ownerKey} ownerKey={ownerKey}
    authenticated={status === "authenticated" && Boolean(session?.user?.id)} />;
}
