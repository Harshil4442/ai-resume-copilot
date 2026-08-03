"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  BookOpenCheck,
  BrainCircuit,
  BriefcaseBusiness,
  CalendarPlus,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Download,
  FileCheck2,
  FilePlus2,
  Gauge,
  LoaderCircle,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  Trophy,
  UserPlus,
  WandSparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LoadingBlock } from "../../../components/ui/LoadingBlock";
import { StatusBadge } from "../../../components/ui/StatusBadge";
import { ApiError, apiDownload, apiGet, apiPatchJson, apiPostJson } from "../../../lib/api";
import {
  type AnalysisResult,
  type AnalysisRun,
  type EvidenceImport,
  type EvidenceItem,
  type OpportunityDetail,
  type OpportunityMatch,
  type Reminder,
  type ResumeVersion,
  type SkillRoi,
  stageLabels,
  stageTone,
  stages,
} from "../../../lib/career";
import { trackEvent } from "../../../lib/analytics";

type Tab = "overview" | "resume" | "learning" | "interview" | "activity" | "outcome";
type Outcome = "offer_accepted" | "offer_declined" | "rejected" | "withdrawn";
type InterviewResult = {
  opportunity_id: string;
  questions: {
    question: string;
    answer: string;
    answer_state?: "evidence_backed" | "evidence_needed";
    evidence_ids?: string[];
  }[];
};
type TailorResult = {
  resume_version_id: string;
  version_number: number;
  evidence_ids: string[];
  content: {
    summary_items?: { text: string; evidence_ids: string[] }[];
    bullets?: { text: string; evidence_ids: string[] }[];
    evidence_needed?: string[];
  };
};
type ResumeList = { resumes: { id: number; filename: string; created_at: string }[] };

const tabs: { id: Tab; label: string; icon: typeof Target }[] = [
  { id: "overview", label: "Overview", icon: Target },
  { id: "resume", label: "Resume & evidence", icon: FileCheck2 },
  { id: "learning", label: "Skill ROI", icon: BookOpenCheck },
  { id: "interview", label: "Interview", icon: MessageSquareText },
  { id: "outcome", label: "Outcome", icon: Trophy },
  { id: "activity", label: "Activity", icon: Activity },
];

const terminal = new Set(["succeeded", "failed", "cancelled"]);

function useRun(runId: string | null) {
  return useQuery({
    queryKey: ["analysis-run", runId],
    queryFn: () => apiGet<AnalysisRun>(`/v1/analysis-runs/${runId}`),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const run = query.state.data as AnalysisRun | undefined;
      return run && terminal.has(run.status) ? false : 1_200;
    },
  });
}

function useRunResult(run: AnalysisRun | undefined) {
  return useQuery({
    queryKey: ["analysis-result", run?.id],
    queryFn: () => apiGet<AnalysisResult>(`/v1/analysis-runs/${run!.id}/result`),
    enabled: run?.status === "succeeded",
  });
}

function MatchSummary({ match }: { match: OpportunityMatch }) {
  const score = Math.round(match.match_score);
  return (
    <div className="grid gap-7 lg:grid-cols-[180px_1fr]">
      <div className="border-l-2 border-primary pl-5">
        <p className="data-label">Role match</p>
        <p className="mt-2 text-5xl font-black text-primary">{score}</p>
        <p className="mt-1 text-sm font-bold text-neutral-400">Grade {match.grade}</p>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/8">
          <div className="h-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
        </div>
      </div>
      <div className="min-w-0">
        <h2 className="text-lg font-black text-neutral-100">Evidence-aware assessment</h2>
        <p className="mt-2 text-sm leading-6 text-neutral-400">{match.fit_summary || "Your role-specific summary will appear here."}</p>
        <div className="mt-5 flex flex-wrap gap-2">
          {match.full_matches.slice(0, 8).map((skill) => (
            <span key={skill} className="rounded-md border border-primary/20 bg-primary/8 px-2 py-1 text-xs font-semibold text-[#69debd]">{skill}</span>
          ))}
          {match.true_gaps.slice(0, 8).map((skill) => (
            <span key={skill} className="rounded-md border border-coral/20 bg-coral/8 px-2 py-1 text-xs font-semibold text-[#ffab9e]">{skill}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function RunFeedback({ run }: { run: AnalysisRun | undefined }) {
  if (!run || run.status === "succeeded") return null;
  if (run.status === "failed") {
    return (
      <div className="mt-4 flex gap-3 border-y border-coral/25 bg-coral/5 px-4 py-4 text-sm text-[#ffab9e]" role="alert">
        <CircleAlert size={18} className="mt-0.5 shrink-0" />
        <div><strong>Analysis did not complete.</strong><p className="mt-1 text-neutral-400">Reserved units were released automatically. Try again later.</p></div>
      </div>
    );
  }
  if (run.status === "cancelled") return <p className="mt-4 text-sm text-neutral-500">Analysis cancelled. Reserved units were released.</p>;
  return (
    <div className="mt-4 flex items-center gap-3 border-y border-primary/20 bg-primary/5 px-4 py-4 text-sm text-neutral-300" role="status">
      <LoaderCircle size={18} className="animate-spin text-primary" />
      <span>{run.status === "queued" ? "Analysis is queued" : "Analyzing the role against your approved resume evidence"}</span>
    </div>
  );
}

export default function OpportunityPage() {
  const params = useParams<{ id: string }>();
  const opportunityId = params.id;
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [matchRunId, setMatchRunId] = useState<string | null>(null);
  const [interviewRunId, setInterviewRunId] = useState<string | null>(null);
  const [tailorRunId, setTailorRunId] = useState<string | null>(null);
  const [reminderMessage, setReminderMessage] = useState("");
  const [reminderDate, setReminderDate] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactRole, setContactRole] = useState("");
  const [submittedVersionId, setSubmittedVersionId] = useState("");
  const [stageError, setStageError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("offer_accepted");
  const [outcomeNotes, setOutcomeNotes] = useState("");
  const [editingEvidenceId, setEditingEvidenceId] = useState<string | null>(null);
  const [evidenceDraft, setEvidenceDraft] = useState("");
  const trackedTerminalRuns = useRef(new Set<string>());

  const opportunity = useQuery({
    queryKey: ["opportunity", opportunityId],
    queryFn: () => apiGet<OpportunityDetail>(`/v1/opportunities/${opportunityId}`),
  });
  const resumes = useQuery({
    queryKey: ["resumes"],
    queryFn: () => apiGet<ResumeList>("/resume/list"),
  });
  const match = useQuery({
    queryKey: ["opportunity-match", opportunityId],
    queryFn: () => apiGet<OpportunityMatch>(`/v1/opportunities/${opportunityId}/match`),
    enabled: Boolean(opportunity.data?.latest_match_id),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
  const evidence = useQuery({
    queryKey: ["evidence", opportunity.data?.resume_id],
    queryFn: () => apiGet<EvidenceItem[]>(`/v1/evidence-items?resume_id=${opportunity.data!.resume_id}`),
    enabled: Boolean(opportunity.data?.resume_id) && tab === "resume",
  });
  const skillRoi = useQuery({
    queryKey: ["skill-roi"],
    queryFn: () => apiGet<SkillRoi>("/v1/skill-roi"),
    enabled: tab === "learning",
  });
  const matchRun = useRun(matchRunId);
  const matchRunResult = useRunResult(matchRun.data);
  const interviewRun = useRun(interviewRunId);
  const interviewResult = useRunResult(interviewRun.data);
  const tailorRun = useRun(tailorRunId);
  const tailorResult = useRunResult(tailorRun.data);

  useEffect(() => {
    if (matchRun.data?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["opportunity-match", opportunityId] });
      void queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
      void queryClient.invalidateQueries({ queryKey: ["nav-profile"] });
    }
  }, [matchRun.data?.status, opportunityId, queryClient]);

  useEffect(() => {
    if (tailorRun.data?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
      void queryClient.invalidateQueries({ queryKey: ["nav-profile"] });
    }
  }, [opportunityId, queryClient, tailorRun.data?.status]);

  useEffect(() => {
    for (const run of [matchRun.data, interviewRun.data, tailorRun.data]) {
      if (!run || !terminal.has(run.status) || trackedTerminalRuns.current.has(run.id)) continue;
      trackedTerminalRuns.current.add(run.id);
      trackEvent(run.status === "succeeded" ? "analysis_completed" : "analysis_failed", {
        run_id: run.id,
        operation: run.operation,
        status: run.status,
        committed_units: run.committed_units,
      });
      if (run.status === "succeeded" && run.operation === "job_match") {
        trackEvent("first_useful_match", { opportunity_id: opportunityId });
      }
    }
  }, [interviewRun.data, matchRun.data, opportunityId, tailorRun.data]);

  const transition = useMutation({
    mutationFn: ({ stage, resumeVersionId }: { stage: string; resumeVersionId?: string }) =>
      apiPostJson<OpportunityDetail>(`/v1/opportunities/${opportunityId}/stage`, {
        stage,
        resume_version_id: resumeVersionId || null,
      }),
    onSuccess: async (_, variables) => {
      setStageError(null);
      trackEvent("opportunity_stage_changed", { stage: variables.stage });
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });
  const recordOutcome = useMutation({
    mutationFn: () => apiPostJson<OpportunityDetail>(`/v1/opportunities/${opportunityId}/outcome`, {
      outcome,
      notes: outcomeNotes.trim() || null,
    }),
    onSuccess: async (updated) => {
      trackEvent("opportunity_outcome_recorded", { outcome: updated.outcome || outcome });
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });
  const exportOpportunity = useMutation({
    mutationFn: () => apiDownload(
      `/v1/opportunities/${opportunityId}/export`,
      `hirewiz-opportunity-${opportunityId}.json`,
    ),
    onSuccess: () => trackEvent("opportunity_exported", { opportunity_id: opportunityId }),
  });
  const connectResume = useMutation({
    mutationFn: (resumeId: string) => apiPatchJson(`/v1/opportunities/${opportunityId}`, { resume_id: resumeId ? Number(resumeId) : null }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
      await queryClient.invalidateQueries({ queryKey: ["opportunity-match", opportunityId] });
    },
  });
  const startMatch = useMutation({
    mutationFn: () => {
      if (!opportunity.data?.resume_id) throw new Error("Connect a resume before running a match.");
      return apiPostJson<AnalysisRun>(
        "/v1/analysis-runs",
        {
          operation: "job_match",
          opportunity_id: opportunityId,
          input: { resume_id: opportunity.data.resume_id },
        },
        { "Idempotency-Key": crypto.randomUUID() },
      );
    },
    onSuccess: (run) => {
      setMatchRunId(run.id);
      trackEvent("analysis_run_created", { operation: "job_match", estimated_units: run.estimated_units });
    },
  });
  const startInterview = useMutation({
    mutationFn: () => apiPostJson<AnalysisRun>(
      "/v1/analysis-runs",
      { operation: "interview_questions", opportunity_id: opportunityId, input: { num_questions: 8 } },
      { "Idempotency-Key": crypto.randomUUID() },
    ),
    onSuccess: (run) => {
      setInterviewRunId(run.id);
      trackEvent("analysis_run_created", { operation: "interview_questions", estimated_units: run.estimated_units });
    },
  });
  const startTailoring = useMutation({
    mutationFn: () => apiPostJson<AnalysisRun>(
      "/v1/analysis-runs",
      { operation: "resume_tailor", opportunity_id: opportunityId, input: {} },
      { "Idempotency-Key": crypto.randomUUID() },
    ),
    onSuccess: (run) => {
      setTailorRunId(run.id);
      trackEvent("analysis_run_created", { operation: "resume_tailor", estimated_units: run.estimated_units });
    },
  });
  const importEvidence = useMutation({
    mutationFn: () => apiPostJson<EvidenceImport>(`/v1/evidence-items/import-resume/${opportunity.data!.resume_id}`, {}),
    onSuccess: async (result) => {
      trackEvent("resume_evidence_imported", { created: result.created.length, skipped: result.skipped });
      await queryClient.invalidateQueries({ queryKey: ["evidence", opportunity.data?.resume_id] });
    },
  });
  const approveEvidence = useMutation({
    mutationFn: ({ id, state }: { id: string; state: "approved" | "rejected" }) =>
      apiPatchJson<EvidenceItem>(`/v1/evidence-items/${id}`, { approval_state: state }),
    onSuccess: (_, variables) => {
      trackEvent(variables.state === "approved" ? "evidence_approved" : "evidence_rejected", {
        opportunity_id: opportunityId,
      });
      return queryClient.invalidateQueries({ queryKey: ["evidence", opportunity.data?.resume_id] });
    },
  });
  const editEvidence = useMutation({
    mutationFn: ({ id, evidenceText }: { id: string; evidenceText: string }) =>
      apiPatchJson<EvidenceItem>(`/v1/evidence-items/${id}`, { evidence_text: evidenceText }),
    onSuccess: async () => {
      trackEvent("evidence_edited", { opportunity_id: opportunityId });
      setEditingEvidenceId(null);
      setEvidenceDraft("");
      await queryClient.invalidateQueries({ queryKey: ["evidence", opportunity.data?.resume_id] });
    },
  });
  const updateVersion = useMutation({
    mutationFn: ({ id, state }: { id: string; state: "approved" | "rejected" }) =>
      apiPatchJson<ResumeVersion>(`/v1/resume-versions/${id}`, { approval_state: state }),
    onSuccess: async (_, variables) => {
      trackEvent("resume_version_reviewed", { approval_state: variables.state });
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
    },
  });
  const createVersion = useMutation({
    mutationFn: () => {
      if (!opportunity.data?.resume_id) throw new Error("Connect a resume first.");
      const evidenceIds = (evidence.data || []).filter((item) => item.approval_state === "approved").map((item) => item.id);
      return apiPostJson<ResumeVersion>("/v1/resume-versions", {
        resume_id: opportunity.data.resume_id,
        opportunity_id: opportunityId,
        label: `${opportunity.data.company || opportunity.data.title} application`,
        evidence_ids: evidenceIds,
      });
    },
    onSuccess: async () => {
      trackEvent("resume_version_created", { opportunity_id: opportunityId });
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
    },
  });
  const createReminder = useMutation({
    mutationFn: () => apiPostJson<Reminder>("/v1/reminders", {
      opportunity_id: opportunityId,
      message: reminderMessage,
      due_at: new Date(reminderDate).toISOString(),
      reminder_type: "follow_up",
    }),
    onSuccess: async () => {
      setReminderMessage("");
      setReminderDate("");
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
    },
  });
  const completeReminder = useMutation({
    mutationFn: (id: string) => apiPatchJson<Reminder>(`/v1/reminders/${id}`, { status: "completed" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] }),
  });
  const createContact = useMutation({
    mutationFn: () => apiPostJson(`/v1/opportunities/${opportunityId}/contacts`, { name: contactName, role: contactRole || null }),
    onSuccess: async () => {
      setContactName("");
      setContactRole("");
      await queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
    },
  });

  const latestMatch = useMemo(() => {
    if (matchRunResult.data?.result) return matchRunResult.data.result as unknown as OpportunityMatch;
    return match.data;
  }, [match.data, matchRunResult.data?.result]);

  if (opportunity.isLoading) return <main className="app-page"><div className="page-container"><LoadingBlock rows={7} /></div></main>;
  if (opportunity.isError || !opportunity.data) {
    return (
      <main className="app-page"><div className="page-container"><EmptyState icon={BriefcaseBusiness} title="Opportunity unavailable" description={opportunity.error instanceof Error ? opportunity.error.message : "This workspace could not be loaded."} action={<Button asChild variant="secondary"><Link href="/workspace"><ArrowLeft size={16} /> Back to workspace</Link></Button>} /></div></main>
    );
  }

  const item = opportunity.data;
  const effectiveSubmittedVersionId = submittedVersionId || item.activity.find(
    (event) => event.to_stage === "applied" && event.resume_version_id,
  )?.resume_version_id || item.resume_versions[0]?.id || "";
  const approvedCount = (evidence.data || []).filter((entry) => entry.approval_state === "approved").length;
  const questions = interviewResult.data?.result as unknown as InterviewResult | undefined;
  const tailored = tailorResult.data?.result as unknown as TailorResult | undefined;

  return (
    <main className="app-page">
      <div className="page-container">
        <Link href="/workspace" className="inline-flex items-center gap-2 text-sm font-semibold text-neutral-500 hover:text-neutral-200"><ArrowLeft size={16} /> Opportunities</Link>
        <header className="mt-5 grid gap-6 border-b border-white/10 pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge tone={stageTone[item.stage]}>{stageLabels[item.stage]}</StatusBadge>
              <span className="text-xs font-bold text-neutral-600">{item.priority} priority</span>
            </div>
            <h1 className="mt-3 text-3xl font-black leading-tight text-neutral-100 sm:text-4xl">{item.title}</h1>
            <p className="mt-2 text-sm font-semibold text-neutral-400">{item.company || "Company not set"}{item.location ? ` · ${item.location}` : ""}</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="grid gap-1 text-xs font-bold text-neutral-500">
              Resume
              <select className="field-control min-w-44" value={item.resume_id || ""} onChange={(event) => connectResume.mutate(event.target.value)} disabled={connectResume.isPending}>
                <option value="">Not connected</option>
                {(resumes.data?.resumes || []).map((resume) => <option key={resume.id} value={resume.id}>{resume.filename}</option>)}
              </select>
            </label>
            <label className="grid gap-1 text-xs font-bold text-neutral-500">
              Application stage
              <select
                className="field-control min-w-44"
                value={item.stage}
                onChange={(event) => {
                  const nextStage = event.target.value;
                  if (nextStage === "applied" && !effectiveSubmittedVersionId) {
                    setStageError("Create and select the exact resume version you submitted.");
                    return;
                  }
                  transition.mutate({
                    stage: nextStage,
                    resumeVersionId: nextStage === "applied" ? effectiveSubmittedVersionId : undefined,
                  });
                }}
                disabled={transition.isPending}
              >
                {stages.map((value) => <option key={value} value={value}>{stageLabels[value]}</option>)}
              </select>
            </label>
            {item.resume_versions.length ? (
              <label className="grid gap-1 text-xs font-bold text-neutral-500">
                Submitted version
                <select className="field-control min-w-44" value={effectiveSubmittedVersionId} onChange={(event) => setSubmittedVersionId(event.target.value)}>
                  {item.resume_versions.map((version) => <option key={version.id} value={version.id}>Version {version.version_number}: {version.label}</option>)}
                </select>
              </label>
            ) : null}
            <Button onClick={() => startMatch.mutate()} disabled={startMatch.isPending || Boolean(matchRun.data && !terminal.has(matchRun.data.status))}>
              {matchRun.data && !terminal.has(matchRun.data.status) ? <LoaderCircle size={16} className="animate-spin" /> : <Sparkles size={16} />}
              {latestMatch ? "Refresh match" : "Run match"}
            </Button>
            <Button size="icon" variant="secondary" onClick={() => exportOpportunity.mutate()} disabled={exportOpportunity.isPending} aria-label="Export opportunity" title="Export opportunity">
              <Download size={16} />
            </Button>
          </div>
        </header>

        {(startMatch.isError || matchRun.data) ? <RunFeedback run={matchRun.data} /> : null}
        {startMatch.isError ? <p className="mt-3 text-sm text-coral">{startMatch.error instanceof Error ? startMatch.error.message : "Could not start analysis."}</p> : null}
        {stageError || transition.isError ? <p className="mt-3 text-sm text-coral">{stageError || (transition.error instanceof Error ? transition.error.message : "Could not update application stage.")}</p> : null}
        {exportOpportunity.isError ? <p className="mt-3 text-sm text-coral">{exportOpportunity.error instanceof Error ? exportOpportunity.error.message : "Could not export this opportunity."}</p> : null}

        <nav className="mt-7 flex max-w-full gap-1 overflow-x-auto border-b border-white/10" aria-label="Opportunity sections">
          {tabs.map((entry) => (
            <button key={entry.id} type="button" onClick={() => setTab(entry.id)} className={`relative flex min-h-11 shrink-0 items-center gap-2 px-3 text-sm font-bold ${tab === entry.id ? "text-white" : "text-neutral-500 hover:text-neutral-300"}`}>
              <entry.icon size={15} /> {entry.label}
              {tab === entry.id ? <span className="absolute inset-x-3 bottom-0 h-0.5 bg-primary" /> : null}
            </button>
          ))}
        </nav>

        <div className="mt-8">
          {tab === "overview" ? (
            <div className="grid gap-10 xl:grid-cols-[1fr_340px]">
              <section className="min-w-0">
                {latestMatch ? <MatchSummary match={latestMatch} /> : (
                  <EmptyState icon={Gauge} title="No match analysis yet" description="Connect a resume and run a match to compare this role with your approved career evidence." action={<Button onClick={() => startMatch.mutate()}><Sparkles size={16} /> Run match</Button>} />
                )}
                {latestMatch?.improvement_tips.length ? (
                  <div className="mt-10 border-t border-white/10 pt-7">
                    <h2 className="text-lg font-black text-neutral-100">Priority improvements</h2>
                    <ul className="mt-4 grid gap-3">
                      {latestMatch.improvement_tips.slice(0, 6).map((tip) => <li key={tip} className="flex gap-3 text-sm leading-6 text-neutral-400"><CheckCircle2 size={17} className="mt-1 shrink-0 text-primary" /> {tip}</li>)}
                    </ul>
                  </div>
                ) : null}
                <div className="mt-10 border-t border-white/10 pt-7">
                  <h2 className="text-lg font-black text-neutral-100">Role snapshot</h2>
                  <p className="mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-neutral-500">{item.job_description}</p>
                </div>
              </section>

              <aside className="space-y-8">
                <section>
                  <div className="flex items-center justify-between"><h2 className="text-base font-black">Reminders</h2><CalendarPlus size={17} className="text-accent" /></div>
                  <div className="mt-4 space-y-2">
                    {item.reminders.filter((reminder) => reminder.status === "scheduled").map((reminder) => (
                      <div key={reminder.id} className="surface-soft flex gap-3 p-3 text-sm">
                        <Clock3 size={15} className="mt-0.5 shrink-0 text-accent" />
                        <div className="min-w-0 flex-1"><p className="font-semibold text-neutral-300">{reminder.message}</p><p className="mt-1 text-xs text-neutral-600">{new Date(reminder.due_at).toLocaleString("en-IN")}</p></div>
                        <button type="button" onClick={() => completeReminder.mutate(reminder.id)} className="text-neutral-600 hover:text-primary" aria-label="Complete reminder"><Check size={16} /></button>
                      </div>
                    ))}
                    {!item.reminders.some((reminder) => reminder.status === "scheduled") ? <p className="text-sm text-neutral-600">No upcoming reminders.</p> : null}
                  </div>
                  <div className="mt-4 grid gap-2">
                    <input className="field-control" value={reminderMessage} onChange={(event) => setReminderMessage(event.target.value)} placeholder="Follow-up reminder" />
                    <input className="field-control" type="datetime-local" value={reminderDate} onChange={(event) => setReminderDate(event.target.value)} aria-label="Reminder date and time" />
                    <Button size="sm" variant="secondary" disabled={!reminderMessage.trim() || !reminderDate || createReminder.isPending} onClick={() => createReminder.mutate()}><Plus size={14} /> Add reminder</Button>
                  </div>
                </section>

                <section className="border-t border-white/10 pt-7">
                  <div className="flex items-center justify-between"><h2 className="text-base font-black">Contacts</h2><UserPlus size={17} className="text-primary" /></div>
                  <div className="mt-4 space-y-2">
                    {item.contacts.map((contact) => <div key={contact.id} className="surface-soft p-3"><p className="text-sm font-bold text-neutral-300">{contact.name}</p><p className="mt-1 text-xs text-neutral-600">{contact.role || "Contact"}</p></div>)}
                    {!item.contacts.length ? <p className="text-sm text-neutral-600">No recruiter or referral contacts yet.</p> : null}
                  </div>
                  <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
                    <input className="field-control" value={contactName} onChange={(event) => setContactName(event.target.value)} placeholder="Contact name" />
                    <input className="field-control" value={contactRole} onChange={(event) => setContactRole(event.target.value)} placeholder="Role or relationship" />
                    <Button size="sm" variant="secondary" disabled={!contactName.trim() || createContact.isPending} onClick={() => createContact.mutate()}><Plus size={14} /> Add contact</Button>
                  </div>
                </section>
              </aside>
            </div>
          ) : null}

          {tab === "resume" ? (
            <div className="grid gap-10 xl:grid-cols-[1fr_360px]">
              <section>
                <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                  <div><p className="eyebrow">Evidence Graph</p><h2 className="mt-2 text-2xl font-black">Approved facts for this resume</h2><p className="mt-2 text-sm text-neutral-500">{approvedCount} approved evidence {approvedCount === 1 ? "item" : "items"}.</p></div>
                  <Button variant="secondary" onClick={() => importEvidence.mutate()} disabled={!item.resume_id || importEvidence.isPending}><RefreshCw size={15} /> Import from resume</Button>
                </div>
                {!item.resume_id ? <EmptyState icon={FilePlus2} title="Connect a resume first" description="Edit this opportunity and choose a parsed resume before building evidence." /> : null}
                {evidence.isLoading ? <div className="mt-6"><LoadingBlock rows={4} /></div> : null}
                {item.resume_id && !evidence.isLoading && !(evidence.data || []).length ? <EmptyState icon={ShieldCheck} title="No evidence imported" description="Import the resume sections, then approve only the facts you want HireWiz to reuse." action={<Button onClick={() => importEvidence.mutate()}><FilePlus2 size={16} /> Import evidence</Button>} /> : null}
                <div className="mt-6 divide-y divide-white/10 border-t border-white/10">
                  {(evidence.data || []).map((entry) => (
                    <article key={entry.id} className="grid gap-4 py-5 sm:grid-cols-[1fr_auto]">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2"><h3 className="font-black text-neutral-200">{entry.title}</h3><StatusBadge tone={entry.approval_state === "approved" ? "teal" : entry.approval_state === "rejected" ? "coral" : "neutral"}>{entry.approval_state}</StatusBadge></div>
                        {editingEvidenceId === entry.id ? <div className="mt-3"><label className="sr-only" htmlFor={`evidence-${entry.id}`}>Edit {entry.title} evidence</label><textarea id={`evidence-${entry.id}`} className="field-control min-h-32 resize-y" value={evidenceDraft} onChange={(event) => setEvidenceDraft(event.target.value)} /><p className="mt-2 text-xs text-neutral-500">Saving a factual edit returns this item to pending review.</p><div className="mt-3 flex gap-2"><Button size="sm" onClick={() => editEvidence.mutate({ id: entry.id, evidenceText: evidenceDraft.trim() })} disabled={evidenceDraft.trim().length < 2 || editEvidence.isPending}>Save edit</Button><Button size="sm" variant="ghost" onClick={() => { setEditingEvidenceId(null); setEvidenceDraft(""); }} disabled={editEvidence.isPending}>Cancel</Button></div></div> : <p className="mt-2 line-clamp-3 text-sm leading-6 text-neutral-500">{entry.evidence_text}</p>}
                        {entry.skills.length ? <p className="mt-2 text-xs text-neutral-600">{entry.skills.slice(0, 8).join(" · ")}</p> : null}
                      </div>
                      <div className="flex items-start gap-1"><Button size="icon" variant="ghost" onClick={() => { setEditingEvidenceId(entry.id); setEvidenceDraft(entry.evidence_text); }} aria-label="Edit evidence"><Pencil size={16} /></Button><Button size="icon" variant="ghost" onClick={() => approveEvidence.mutate({ id: entry.id, state: "approved" })} aria-label="Approve evidence"><Check size={17} /></Button><Button size="icon" variant="ghost" onClick={() => approveEvidence.mutate({ id: entry.id, state: "rejected" })} aria-label="Reject evidence"><X size={17} /></Button></div>
                    </article>
                  ))}
                </div>
              </section>
              <aside>
                <div className="flex items-center justify-between"><div><p className="data-label">Resume versions</p><h2 className="mt-1 text-lg font-black">Application history</h2></div><FileCheck2 size={19} className="text-primary" /></div>
                <div className="mt-5 space-y-3">
                  {item.resume_versions.map((version) => (
                    <div key={version.id} className="surface-soft p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-bold text-neutral-200">{version.label}</p>
                          <p className="mt-1 text-xs text-neutral-600">Version {version.version_number} · {version.evidence_ids.length} evidence links{version.submitted_at ? " · submitted" : ""}</p>
                        </div>
                        <StatusBadge tone={version.approval_state === "approved" ? "teal" : version.approval_state === "rejected" ? "coral" : "neutral"}>{version.approval_state}</StatusBadge>
                      </div>
                      <div className="mt-3 flex gap-2 border-t border-white/10 pt-3">
                        <Button size="sm" variant="ghost" onClick={() => updateVersion.mutate({ id: version.id, state: "approved" })}><Check size={14} /> Approve</Button>
                        <Button size="sm" variant="ghost" onClick={() => updateVersion.mutate({ id: version.id, state: "rejected" })}><X size={14} /> Reject</Button>
                      </div>
                    </div>
                  ))}
                  {!item.resume_versions.length ? <p className="text-sm leading-6 text-neutral-600">No role-specific version has been saved.</p> : null}
                </div>
                <Button
                  className="mt-5 w-full"
                  disabled={approvedCount === 0 || startTailoring.isPending || Boolean(tailorRun.data && !terminal.has(tailorRun.data.status))}
                  onClick={() => startTailoring.mutate()}
                >
                  {tailorRun.data && !terminal.has(tailorRun.data.status) ? <LoaderCircle size={16} className="animate-spin" /> : <WandSparkles size={16} />}
                  Generate evidence-backed version
                </Button>
                <RunFeedback run={tailorRun.data} />
                {startTailoring.isError ? <p className="mt-3 text-sm text-coral">{startTailoring.error instanceof Error ? startTailoring.error.message : "Could not start tailoring."}</p> : null}
                {tailored ? (
                  <div className="mt-4 border-l-2 border-primary pl-4">
                    <p className="text-sm font-bold text-neutral-200">Version {tailored.version_number} created</p>
                    <p className="mt-1 text-xs leading-5 text-neutral-500">Every generated line links back to approved evidence. Review it before marking the version approved.</p>
                  </div>
                ) : null}
                <Button className="mt-5 w-full" disabled={!item.resume_id || createVersion.isPending} onClick={() => createVersion.mutate()}><FilePlus2 size={16} /> Save current version</Button>
                {createVersion.isError ? <p className="mt-3 text-sm text-coral">{createVersion.error instanceof Error ? createVersion.error.message : "Could not create version."}</p> : null}
              </aside>
            </div>
          ) : null}

          {tab === "learning" ? (
            <section>
              <div className="max-w-2xl"><p className="eyebrow">Skill ROI</p><h2 className="mt-2 text-2xl font-black">What is worth learning next</h2><p className="mt-2 text-sm leading-6 text-neutral-500">Ranked across your active opportunities and approved evidence.</p></div>
              {skillRoi.isLoading ? <div className="mt-7"><LoadingBlock rows={5} /></div> : null}
              {!skillRoi.isLoading && !skillRoi.data?.items.length ? <EmptyState icon={BookOpenCheck} title="Skill ROI needs match data" description="Run matches for active opportunities to see which skill investment has the strongest return." /> : null}
              <div className="mt-7 divide-y divide-white/10 border-y border-white/10">
                {(skillRoi.data?.items || []).map((entry, index) => (
                  <article key={entry.skill} className="grid gap-4 py-5 sm:grid-cols-[44px_1fr_auto] sm:items-center">
                    <span className="text-2xl font-black text-neutral-700">{String(index + 1).padStart(2, "0")}</span>
                    <div><h3 className="font-black text-neutral-200">{entry.skill}</h3><p className="mt-1 text-sm text-neutral-500">{entry.reason}</p></div>
                    <div className="sm:text-right"><p className="text-xl font-black text-accent">{Math.round(entry.score)}</p><p className="text-xs text-neutral-600">~{entry.estimated_hours} hours</p></div>
                  </article>
                ))}
              </div>
            </section>
          ) : null}

          {tab === "interview" ? (
            <section>
              <div className="flex flex-col gap-5 border-b border-white/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
                <div className="max-w-2xl"><p className="eyebrow">Role-specific preparation</p><h2 className="mt-2 text-2xl font-black">Interview questions</h2><p className="mt-2 text-sm leading-6 text-neutral-500">Generated from the preserved role. Keep answers grounded in approved evidence.</p></div>
                <Button onClick={() => startInterview.mutate()} disabled={startInterview.isPending || Boolean(interviewRun.data && !terminal.has(interviewRun.data.status))}>{interviewRun.data && !terminal.has(interviewRun.data.status) ? <LoaderCircle size={16} className="animate-spin" /> : <BrainCircuit size={16} />} Generate questions</Button>
              </div>
              <RunFeedback run={interviewRun.data} />
              <div className="divide-y divide-white/10">
                {(questions?.questions || []).map((question, index) => (
                  <article key={`${index}-${question.question}`} className="py-6"><div className="flex gap-4"><span className="text-sm font-black text-primary">{String(index + 1).padStart(2, "0")}</span><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-black text-neutral-200">{question.question}</h3><StatusBadge tone={question.answer_state === "evidence_backed" ? "teal" : "amber"}>{question.answer_state === "evidence_backed" ? `${question.evidence_ids?.length || 0} sources` : "evidence needed"}</StatusBadge></div>{question.answer ? <p className="mt-3 whitespace-pre-line text-sm leading-6 text-neutral-500">{question.answer}</p> : null}</div></div></article>
                ))}
              </div>
              {!questions && !interviewRun.data ? <EmptyState icon={MessageSquareText} title="Prepare from the actual role" description="Generate a focused question set from this opportunity's job snapshot." /> : null}
            </section>
          ) : null}

          {tab === "outcome" ? (
            <section className="grid gap-10 lg:grid-cols-[1fr_360px]">
              <div className="max-w-2xl">
                <p className="eyebrow">Outcome learning</p>
                <h2 className="mt-2 text-2xl font-black">Close the loop</h2>
                <p className="mt-2 text-sm leading-6 text-neutral-500">
                  Record what happened so future Skill ROI and application decisions can learn from your own history.
                </p>
                {item.outcome ? (
                  <div className="mt-7 border-l-2 border-primary pl-5">
                    <p className="data-label">Recorded outcome</p>
                    <p className="mt-2 text-lg font-black text-neutral-200">{item.outcome.replaceAll("_", " ")}</p>
                    {item.outcome_notes ? <p className="mt-2 text-sm leading-6 text-neutral-500">{item.outcome_notes}</p> : null}
                    {item.outcome_at ? <p className="mt-2 text-xs text-neutral-700">{new Date(item.outcome_at).toLocaleString("en-IN")}</p> : null}
                  </div>
                ) : (
                  <EmptyState icon={Trophy} title="No final outcome recorded" description="You can update this later without losing the application timeline." />
                )}
              </div>
              <div className="border-t border-white/10 pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
                <label className="grid gap-2 text-sm font-semibold text-neutral-300">
                  Final outcome
                  <select className="field-control" value={outcome} onChange={(event) => setOutcome(event.target.value as Outcome)}>
                    <option value="offer_accepted">Offer accepted</option>
                    <option value="offer_declined">Offer declined</option>
                    <option value="rejected">Rejected</option>
                    <option value="withdrawn">Withdrawn</option>
                  </select>
                </label>
                <label className="mt-4 grid gap-2 text-sm font-semibold text-neutral-300">
                  Notes or feedback
                  <textarea className="field-control min-h-32 resize-y" value={outcomeNotes} onChange={(event) => setOutcomeNotes(event.target.value)} placeholder="Optional recruiter feedback or what you learned" />
                </label>
                <Button className="mt-4 w-full" onClick={() => recordOutcome.mutate()} disabled={recordOutcome.isPending}>
                  <Trophy size={16} /> {recordOutcome.isPending ? "Saving outcome..." : "Record outcome"}
                </Button>
                {recordOutcome.isError ? <p className="mt-3 text-sm text-coral">{recordOutcome.error instanceof Error ? recordOutcome.error.message : "Could not record outcome."}</p> : null}
              </div>
            </section>
          ) : null}

          {tab === "activity" ? (
            <section className="max-w-3xl">
              <p className="eyebrow">History</p><h2 className="mt-2 text-2xl font-black">Application activity</h2>
              <ol className="mt-7 border-l border-white/10 pl-6">
                {item.activity.map((event) => (
                  <li key={event.id} className="relative pb-7"><span className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-[#0f1211] bg-primary" /><p className="text-sm font-bold text-neutral-300">{event.event_type === "stage_changed" ? `${stageLabels[event.from_stage || "saved"]} to ${stageLabels[event.to_stage || "saved"]}` : event.event_type === "outcome_recorded" ? "Outcome recorded" : "Opportunity created"}</p>{event.note ? <p className="mt-1 text-sm text-neutral-500">{event.note}</p> : null}<p className="mt-1 text-xs text-neutral-700">{new Date(event.occurred_at).toLocaleString("en-IN")}</p></li>
                ))}
              </ol>
            </section>
          ) : null}
        </div>
      </div>
    </main>
  );
}
