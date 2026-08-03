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
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { apiGet, apiPostJson } from "../../lib/api";
import {
  type Opportunity,
  type OpportunityList,
  stageLabels,
  stageTone,
  stages,
} from "../../lib/career";
import { trackEvent } from "../../lib/analytics";

type ResumeList = { resumes: { id: number; filename: string; created_at: string }[] };

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

function OpportunityRow({ opportunity }: { opportunity: Opportunity }) {
  return (
    <Link
      href={`/workspace/${opportunity.id}`}
      className="group grid min-h-28 gap-4 border-b border-white/10 px-1 py-5 transition-colors hover:bg-white/[0.025] sm:grid-cols-[1fr_auto] sm:items-center sm:px-4"
    >
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="min-w-0 text-base font-black text-neutral-100 sm:text-lg">{opportunity.title}</h2>
          <StatusBadge tone={stageTone[opportunity.stage]}>{stageLabels[opportunity.stage]}</StatusBadge>
          {opportunity.priority === "high" ? <span className="text-xs font-bold text-accent">High priority</span> : null}
        </div>
        <p className="mt-1 text-sm font-semibold text-neutral-400">{opportunity.company || "Company not set"}</p>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-neutral-600">
          {opportunity.location ? <span className="flex items-center gap-1.5"><MapPin size={13} /> {opportunity.location}</span> : null}
          <span className="flex items-center gap-1.5"><CalendarClock size={13} /> Updated {formatDate(opportunity.updated_at)}</span>
          {opportunity.next_action ? <span>Next: {opportunity.next_action}</span> : null}
        </div>
      </div>
      <ArrowRight size={18} className="hidden text-neutral-600 transition-transform group-hover:translate-x-1 group-hover:text-primary sm:block" aria-hidden="true" />
    </Link>
  );
}

export default function WorkspacePage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [stage, setStage] = useState("all");
  const [search, setSearch] = useState("");
  const opportunities = useQuery({
    queryKey: ["opportunities"],
    queryFn: () => apiGet<OpportunityList>("/v1/opportunities?limit=200"),
  });
  const resumes = useQuery({
    queryKey: ["resumes"],
    queryFn: () => apiGet<ResumeList>("/resume/list"),
  });
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
      trackEvent("opportunity_created", { source: created.source, has_resume: Boolean(created.resume_id) });
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      setDialogOpen(false);
      form.reset();
      router.push(`/workspace/${created.id}`);
    },
  });

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return (opportunities.data?.items || []).filter((item) => {
      const matchesStage = stage === "all" || item.stage === stage;
      const matchesSearch =
        !normalized ||
        `${item.title} ${item.company} ${item.location}`.toLowerCase().includes(normalized);
      return matchesStage && matchesSearch;
    });
  }, [opportunities.data?.items, search, stage]);

  const activeCount = (opportunities.data?.items || []).filter((item) => !["rejected", "withdrawn", "archived"].includes(item.stage)).length;
  const interviewCount = (opportunities.data?.items || []).filter((item) => item.stage === "interviewing").length;

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="flex flex-col gap-6 border-b border-white/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="eyebrow">Career Workspace</p>
            <h1 className="mt-2 text-3xl font-black text-neutral-100 sm:text-4xl">Your opportunities</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-500">
              {activeCount} active {activeCount === 1 ? "role" : "roles"} and {interviewCount} in interview stage.
            </p>
          </div>
          <Button onClick={() => setDialogOpen(true)}>
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
                  aria-selected={stage === value}
                  onClick={() => setStage(value)}
                  className={`min-h-9 shrink-0 rounded-md px-3 text-xs font-bold ${
                    stage === value ? "bg-white/10 text-white" : "text-neutral-500 hover:bg-white/5 hover:text-neutral-300"
                  }`}
                >
                  {value === "all" ? "All" : stageLabels[value]}
                </button>
              ))}
            </div>
            <label className="relative block w-full lg:w-72">
              <span className="sr-only">Search opportunities</span>
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-neutral-600" />
              <input
                className="field-control pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search role or company"
              />
            </label>
          </div>

          <div className="mt-5">
            {opportunities.isLoading ? <LoadingBlock rows={5} /> : null}
            {opportunities.isError ? (
              <div className="border-y border-coral/25 bg-coral/5 px-5 py-6 text-sm text-[#ffab9e]">
                {opportunities.error instanceof Error ? opportunities.error.message : "Could not load opportunities."}
              </div>
            ) : null}
            {!opportunities.isLoading && !opportunities.isError && filtered.length === 0 ? (
              <EmptyState
                icon={BriefcaseBusiness}
                title={opportunities.data?.items.length ? "No opportunities match these filters" : "Add your first target role"}
                description={opportunities.data?.items.length ? "Change the stage or search to return to your active work." : "Preserve the role, connect your resume, and keep every decision in one place."}
                action={!opportunities.data?.items.length ? <Button onClick={() => setDialogOpen(true)}><Plus size={16} /> Add opportunity</Button> : undefined}
              />
            ) : null}
            {filtered.length ? <div className="border-t border-white/10">{filtered.map((item) => <OpportunityRow key={item.id} opportunity={item} />)}</div> : null}
          </div>
        </section>
      </div>

      <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/70 backdrop-blur-sm" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[71] max-h-[90vh] w-[min(94vw,680px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-white/15 bg-[#151817] p-5 shadow-2xl sm:p-7">
            <div className="flex items-start justify-between gap-4">
              <div>
                <Dialog.Title className="text-xl font-black text-neutral-100">Add an opportunity</Dialog.Title>
                <Dialog.Description className="mt-1 text-sm text-neutral-500">The original role snapshot is preserved when you create it.</Dialog.Description>
              </div>
              <Dialog.Close asChild><button type="button" className="icon-button" aria-label="Close"><X size={18} /></button></Dialog.Close>
            </div>

            <form className="mt-6 grid gap-5" onSubmit={form.handleSubmit((values) => createOpportunity.mutate(values))}>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">
                  Role title
                  <input className="field-control" {...form.register("title")} placeholder="Senior Product Designer" />
                  {form.formState.errors.title ? <span className="text-xs text-coral">{form.formState.errors.title.message}</span> : null}
                </label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">
                  Company
                  <input className="field-control" {...form.register("company")} placeholder="Company name" />
                </label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">
                  Location
                  <input className="field-control" {...form.register("location")} placeholder="Bengaluru or remote" />
                </label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">
                  Priority
                  <select className="field-control" {...form.register("priority")}>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300 sm:col-span-2">
                  Source URL
                  <input className="field-control" {...form.register("source_url")} placeholder="https://company.example/careers/role" inputMode="url" />
                </label>
                <label className="grid gap-2 text-sm font-semibold text-neutral-300 sm:col-span-2">
                  Resume
                  <select className="field-control" {...form.register("resume_id")}>
                    <option value="">Connect later</option>
                    {(resumes.data?.resumes || []).map((resume) => <option key={resume.id} value={resume.id}>{resume.filename}</option>)}
                  </select>
                </label>
              </div>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">
                Job description
                <textarea className="field-control min-h-52 resize-y" {...form.register("job_description")} placeholder="Paste the role description" />
                {form.formState.errors.job_description ? <span className="text-xs text-coral">{form.formState.errors.job_description.message}</span> : null}
              </label>
              {createOpportunity.isError ? <p className="text-sm text-coral">{createOpportunity.error instanceof Error ? createOpportunity.error.message : "Could not create opportunity."}</p> : null}
              <div className="flex flex-col-reverse gap-2 border-t border-white/10 pt-5 sm:flex-row sm:justify-end">
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
