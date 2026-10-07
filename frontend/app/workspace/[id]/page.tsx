"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
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
  FileText,
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
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";

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
  type SourcePreservingResumeContent,
  getSourcePreservingContent,
  stageLabels,
  stageTone,
  stages,
} from "../../../lib/career";
import { trackEvent } from "../../../lib/analytics";
import type { ResumeListResponse, ResumeSourceFormat } from "../../../lib/types";

type Tab = "overview" | "resume" | "learning" | "interview" | "activity" | "outcome";
type SourceState = "no_selection" | "loading" | "request_error" | "missing_row" | "unknown_metadata" | "confirmed_absent" | "ready";
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
  content: SourcePreservingResumeContent;
};

const tabs: { id: Tab; label: string; icon: typeof Target }[] = [
  { id: "overview", label: "Overview", icon: Target },
  { id: "resume", label: "Resume & evidence", icon: FileCheck2 },
  { id: "learning", label: "Skill ROI", icon: BookOpenCheck },
  { id: "interview", label: "Interview", icon: MessageSquareText },
  { id: "outcome", label: "Outcome", icon: Trophy },
  { id: "activity", label: "Activity", icon: Activity },
];

const terminal = new Set(["succeeded", "failed", "cancelled"]);
const interviewQuestionCount = 8;

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
        <p className="mt-2 text-5xl font-semibold text-primary">{score}</p>
        <p className="mt-1 text-sm font-bold text-muted-foreground">Grade {match.grade}</p>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface">
          <div className="h-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
        </div>
      </div>
      <div className="min-w-0">
        <h2 className="font-display text-lg font-normal text-foreground">Evidence-aware assessment</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">{match.fit_summary || "Your role-specific summary will appear here."}</p>
        <div className="mt-5 flex flex-wrap gap-2">
          {match.full_matches.slice(0, 8).map((skill) => (
            <span key={skill} className="rounded-md border border-primary/20 bg-primary/8 px-2 py-1 text-xs font-semibold text-primary">{skill}</span>
          ))}
          {match.true_gaps.slice(0, 8).map((skill) => (
            <span key={skill} className="rounded-md border border-coral/20 bg-coral/8 px-2 py-1 text-xs font-semibold text-coral">{skill}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function RunFeedback({ run }: { run: AnalysisRun | undefined }) {
  if (!run || run.status === "succeeded") return null;
  if (run.status === "failed") {
    const incompleteInterview = run.operation === "interview_questions" && run.error_code === "InterviewOutputError";
    const preservationFailed = run.operation === "resume_tailor" && (run.error_code === "ResumeLayoutError" || run.error_code === "TailoringOutputError");
    const usageMessage = run.committed_units === 0 && run.usage_state === "released"
      ? "Any reserved units were released."
      : run.committed_units === 0 && run.usage_state === "waived"
        ? "No analysis units were charged."
        : "";
    return (
      <div className="mt-4 flex gap-3 border-y border-coral/25 bg-coral/5 px-4 py-4 text-sm text-coral" role="alert">
        <CircleAlert size={18} className="mt-0.5 shrink-0" />
        <div><strong>{preservationFailed ? "Could not apply the changes while preserving your resume format." : incompleteInterview ? "Could not generate a complete question set." : "Analysis did not complete."}</strong><p className="mt-1 text-muted-foreground">{incompleteInterview || preservationFailed ? "Please try again." : "Try again later."} {usageMessage}</p></div>
      </div>
    );
  }
  if (run.status === "cancelled") return <p className="mt-4 text-sm text-muted-foreground">Analysis cancelled. Reserved units were released.</p>;
  return (
    <div className="mt-4 flex items-center gap-3 border-y border-primary/20 bg-primary/5 px-4 py-4 text-sm text-foreground" role="status">
      <LoaderCircle size={18} className="animate-spin text-primary" />
      <span>{run.status === "queued" ? "Analysis is queued" : "Analyzing the role against your approved resume evidence"}</span>
    </div>
  );
}

function OpportunityContent() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const opportunityId = params.id;
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>(() => tabs.find((entry) => entry.id === searchParams.get("tab"))?.id || "overview");
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
  const [reviewedVersionIds, setReviewedVersionIds] = useState<Record<string, boolean>>({});
  const trackedTerminalRuns = useRef(new Set<string>());
  const resumeTabRef = useRef<HTMLButtonElement>(null);
  const resumeSelectRef = useRef<HTMLSelectElement>(null);

  const opportunity = useQuery({
    queryKey: ["opportunity", opportunityId],
    queryFn: () => apiGet<OpportunityDetail>(`/v1/opportunities/${opportunityId}`),
  });
  const resumes = useQuery({
    queryKey: ["resumes"],
    queryFn: () => apiGet<ResumeListResponse>("/resume/list"),
  });
  const sourceResume = resumes.data?.resumes.find((resume) => resume.id === opportunity.data?.resume_id);
  const sourceState: SourceState = !opportunity.data?.resume_id ? "no_selection"
    : resumes.isLoading ? "loading"
    : resumes.isError ? "request_error"
    : !sourceResume ? "missing_row"
    : sourceResume.source_available === false ? "confirmed_absent"
    : sourceResume.source_available === true && (sourceResume.source_format === "pdf" || sourceResume.source_format === "docx") ? "ready"
    : "unknown_metadata";
  const sourceReady = sourceState === "ready";
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
      if (run.operation === "interview_questions" || run.operation === "resume_tailor") {
        void queryClient.invalidateQueries({ queryKey: ["nav-profile"] });
      }
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
  }, [interviewRun.data, matchRun.data, opportunityId, queryClient, tailorRun.data]);

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
      { operation: "interview_questions", opportunity_id: opportunityId, input: { num_questions: interviewQuestionCount } },
      { "Idempotency-Key": crypto.randomUUID() },
    ),
    onSuccess: (run) => {
      setInterviewRunId(run.id);
      trackEvent("analysis_run_created", { operation: "interview_questions", estimated_units: run.estimated_units });
    },
  });
  const startTailoring = useMutation({
    mutationFn: () => {
      if (!sourceReady) throw new Error("Check the original file status beside the tailoring actions before generating a version.");
      if (!evidence.isSuccess || !(evidence.data || []).some((entry) => entry.approval_state === "approved")) throw new Error("Import and approve relevant evidence for the selected resume before tailoring.");
      return apiPostJson<AnalysisRun>(
        "/v1/analysis-runs",
        { operation: "resume_tailor", opportunity_id: opportunityId, input: {} },
        { "Idempotency-Key": crypto.randomUUID() },
      );
    },
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
  const downloadVersion = useMutation({
    mutationFn: ({ id, versionNumber, format }: { id: string; versionNumber: number; format: ResumeSourceFormat }) =>
      apiDownload(
        `/v1/resume-versions/${id}/download?format=${format}`,
        `hirewiz-tailored-resume-v${versionNumber}.${format}`,
      ),
    onSuccess: (_, variables) => {
      trackEvent("resume_version_downloaded", {
        opportunity_id: opportunityId,
        resume_version_id: variables.id,
        format: variables.format,
      });
    },
  });
  const downloadOriginal = useMutation({
    mutationFn: () => {
      if (!sourceResume || !sourceReady) throw new Error("The original resume file is unavailable. Upload your source again.");
      return apiDownload(`/resume/${sourceResume.id}/source`, sourceResume.filename);
    },
  });
  const createVersion = useMutation({
    mutationFn: () => {
      if (!opportunity.data?.resume_id || !sourceReady || !sourceResume?.source_format) throw new Error("Connect a resume with its original file first.");
      const evidenceIds = (evidence.data || []).filter((item) => item.approval_state === "approved").map((item) => item.id);
      return apiPostJson<ResumeVersion>("/v1/resume-versions", {
        resume_id: opportunity.data.resume_id,
        opportunity_id: opportunityId,
        label: `${opportunity.data.company || opportunity.data.title} source snapshot`,
        structured_content: { format_preservation: "source", source_format: sourceResume.source_format, source_edits: [] },
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
  const sourceUploadHref = `/resume?opportunity=${encodeURIComponent(opportunityId)}`;
  const sourceIssue = sourceState === "no_selection" ? "Choose a resume above before generating a tailored version or saving a source snapshot."
    : sourceState === "loading" ? "Checking whether the selected resume has its original file. Please wait."
    : sourceState === "request_error" ? "Could not load the selected resume's source details. Refresh details to check its original file."
    : sourceState === "missing_row" ? "The connected resume is missing from the loaded resume list. Refresh details or choose another resume above."
    : sourceState === "unknown_metadata" ? "The selected resume's original file status is unknown. Refresh details before uploading again."
    : sourceState === "confirmed_absent" ? `${approvedCount > 0 ? "Your evidence is approved, but the selected resume's original file is missing." : "The selected resume's original file is missing."} Upload the original PDF or DOCX again, use it for this opportunity, then import and approve the new upload's facts.`
    : null;
  const tailoringIsRunning = startTailoring.isPending || Boolean(tailorRun.data && !terminal.has(tailorRun.data.status));
  const tailoringIssue = connectResume.isPending ? "Connecting the selected resume. Please wait before generating a version or saving a source snapshot." : sourceIssue || (evidence.isLoading ? "Loading evidence for the selected resume before tailoring. You can still save a source snapshot."
    : evidence.isError ? "Could not load evidence for the selected resume. Refresh evidence before tailoring. You can still save a source snapshot."
    : approvedCount === 0 ? "Import and approve relevant evidence above before generating a tailored version. A source snapshot does not require approved evidence."
    : tailoringIsRunning ? "A tailored version is being generated. Wait for it to finish before starting another."
    : createVersion.isPending ? "Saving your source snapshot. Please wait."
    : null);
  const sourceCanRefresh = sourceState === "request_error" || sourceState === "missing_row" || sourceState === "unknown_metadata";
  const questions = interviewResult.data?.result as unknown as InterviewResult | undefined;
  const interviewQuestions = questions?.questions || [];
  const evidenceBackedCount = interviewQuestions.filter((question) => question.answer_state === "evidence_backed").length;
  const interviewIsRunning = startInterview.isPending || Boolean(interviewRun.data && !terminal.has(interviewRun.data.status));
  const tailored = tailorResult.data?.result as unknown as TailorResult | undefined;

  function reviewResumeEvidence() {
    setTab("resume");
    resumeTabRef.current?.focus();
    resumeTabRef.current?.scrollIntoView({ block: "nearest" });
  }

  function chooseResume() {
    resumeSelectRef.current?.focus();
    resumeSelectRef.current?.scrollIntoView({ block: "nearest" });
  }

  function refreshSourceDetails() {
    void Promise.all([opportunity.refetch(), resumes.refetch()]);
  }

  return (
    <main className="app-page">
      <div className="page-container">
        <Link href="/workspace" className="inline-flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground"><ArrowLeft size={16} /> Opportunities</Link>
        <header className="mt-5 space-y-6 border-b border-border pb-7">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge tone={stageTone[item.stage]}>{stageLabels[item.stage]}</StatusBadge>
              <span className="text-xs font-bold text-muted-foreground">{item.priority} priority</span>
            </div>
            <h1 className="font-display mt-3 max-w-4xl break-words text-4xl font-normal leading-tight text-foreground sm:text-5xl">{item.title}</h1>
            <p className="mt-2 text-sm font-semibold text-muted-foreground">{item.company || "Company not set"}{item.location ? ` · ${item.location}` : ""}</p>
          </div>
          <div className={`grid gap-4 rounded-xl border border-border bg-surface/60 p-4 sm:grid-cols-2 sm:p-5 ${item.resume_versions.length ? "xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.2fr)_auto]" : "xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto]"}`}>
            <label className="grid min-w-0 gap-2 text-xs font-bold text-muted-foreground">
              Resume
              <select ref={resumeSelectRef} className="field-control min-w-0 truncate pr-8" title={sourceResume?.filename} value={item.resume_id || ""} onChange={(event) => connectResume.mutate(event.target.value)} disabled={connectResume.isPending}>
                <option value="">Not connected</option>
                {item.resume_id && !sourceResume ? <option value={item.resume_id}>Connected resume #{item.resume_id} · details unavailable</option> : null}
                {(resumes.data?.resumes || []).map((resume) => <option key={resume.id} value={resume.id}>{resume.filename} · #{resume.id} · {resume.source_available === false ? "needs upload" : resume.source_available === true && (resume.source_format === "pdf" || resume.source_format === "docx") ? `${resume.source_format.toUpperCase()} original saved` : "source status unknown"}</option>)}
              </select>
            </label>
            <label className="grid min-w-0 gap-2 text-xs font-bold text-muted-foreground">
              Application stage
              <select
                className="field-control min-w-0"
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
              <label className="grid min-w-0 gap-2 text-xs font-bold text-muted-foreground">
                Submitted version
                <select className="field-control min-w-0 truncate pr-8" title={item.resume_versions.find((version) => version.id === effectiveSubmittedVersionId)?.label} value={effectiveSubmittedVersionId} onChange={(event) => setSubmittedVersionId(event.target.value)}>
                  {item.resume_versions.map((version) => <option key={version.id} value={version.id}>Version {version.version_number}: {version.label}</option>)}
                </select>
              </label>
            ) : null}
            <div className="flex items-end gap-2 sm:col-span-2 xl:col-span-1">
              <Button className="min-h-11 flex-1 whitespace-nowrap xl:flex-none" onClick={() => startMatch.mutate()} disabled={startMatch.isPending || Boolean(matchRun.data && !terminal.has(matchRun.data.status))}>
                {matchRun.data && !terminal.has(matchRun.data.status) ? <LoaderCircle size={16} className="animate-spin" /> : <Sparkles size={16} />}
                {latestMatch ? "Refresh match" : "Run match"}
              </Button>
              <Button className="h-11 w-11 shrink-0" size="icon" variant="secondary" onClick={() => exportOpportunity.mutate()} disabled={exportOpportunity.isPending} aria-label="Export opportunity" title="Export opportunity">
                <Download size={16} />
              </Button>
            </div>
          </div>
        </header>

        {(startMatch.isError || matchRun.data) ? <RunFeedback run={matchRun.data} /> : null}
        {startMatch.isError ? <p className="mt-3 text-sm text-coral">{startMatch.error instanceof Error ? startMatch.error.message : "Could not start analysis."}</p> : null}
        {stageError || transition.isError ? <p className="mt-3 text-sm text-coral">{stageError || (transition.error instanceof Error ? transition.error.message : "Could not update application stage.")}</p> : null}
        {exportOpportunity.isError ? <p className="mt-3 text-sm text-coral">{exportOpportunity.error instanceof Error ? exportOpportunity.error.message : "Could not export this opportunity."}</p> : null}

        <nav className="mt-7 flex max-w-full gap-1 overflow-x-auto border-b border-border" aria-label="Opportunity sections">
          {tabs.map((entry) => (
            <button key={entry.id} ref={entry.id === "resume" ? resumeTabRef : undefined} type="button" aria-pressed={tab === entry.id} onClick={() => setTab(entry.id)} className={`relative flex min-h-11 shrink-0 items-center gap-2 px-3 text-sm font-bold ${tab === entry.id ? "text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
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
                  <div className="mt-10 border-t border-border pt-7">
                    <h2 className="font-display text-lg font-normal text-foreground">Priority improvements</h2>
                    <ul className="mt-4 grid gap-3">
                      {latestMatch.improvement_tips.slice(0, 6).map((tip) => <li key={tip} className="flex gap-3 text-sm leading-6 text-muted-foreground"><CheckCircle2 size={17} className="mt-1 shrink-0 text-primary" /> {tip}</li>)}
                    </ul>
                  </div>
                ) : null}
                <div className="mt-10 border-t border-border pt-7">
                  <h2 className="font-display text-lg font-normal text-foreground">Role snapshot</h2>
                  <p className="mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{item.job_description}</p>
                </div>
              </section>

              <aside className="space-y-8">
                <section>
                  <div className="flex items-center justify-between"><h2 className="font-display text-base font-normal">Reminders</h2><CalendarPlus size={17} className="text-primary" /></div>
                  <div className="mt-4 space-y-2">
                    {item.reminders.filter((reminder) => reminder.status === "scheduled").map((reminder) => (
                      <div key={reminder.id} className="surface-soft flex gap-3 p-3 text-sm">
                        <Clock3 size={15} className="mt-0.5 shrink-0 text-primary" />
                        <div className="min-w-0 flex-1"><p className="font-semibold text-foreground">{reminder.message}</p><p className="mt-1 text-xs text-muted-foreground">{new Date(reminder.due_at).toLocaleString("en-IN")}</p></div>
                        <button type="button" onClick={() => completeReminder.mutate(reminder.id)} className="text-muted-foreground hover:text-primary" aria-label="Complete reminder"><Check size={16} /></button>
                      </div>
                    ))}
                    {!item.reminders.some((reminder) => reminder.status === "scheduled") ? <p className="text-sm text-muted-foreground">No upcoming reminders.</p> : null}
                  </div>
                  <div className="mt-4 grid gap-2">
                    <input className="field-control" value={reminderMessage} onChange={(event) => setReminderMessage(event.target.value)} placeholder="Follow-up reminder" />
                    <input className="field-control" type="datetime-local" value={reminderDate} onChange={(event) => setReminderDate(event.target.value)} aria-label="Reminder date and time" />
                    <Button size="sm" variant="secondary" disabled={!reminderMessage.trim() || !reminderDate || createReminder.isPending} onClick={() => createReminder.mutate()}><Plus size={14} /> Add reminder</Button>
                  </div>
                </section>

                <section className="border-t border-border pt-7">
                  <div className="flex items-center justify-between"><h2 className="font-display text-base font-normal">Contacts</h2><UserPlus size={17} className="text-primary" /></div>
                  <div className="mt-4 space-y-2">
                    {item.contacts.map((contact) => <div key={contact.id} className="surface-soft p-3"><p className="text-sm font-bold text-foreground">{contact.name}</p><p className="mt-1 text-xs text-muted-foreground">{contact.role || "Contact"}</p></div>)}
                    {!item.contacts.length ? <p className="text-sm text-muted-foreground">No recruiter or referral contacts yet.</p> : null}
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
            <div className="space-y-10">
              {item.resume_id ? (
                <section className="surface-soft p-5 sm:p-6" aria-labelledby="source-resume-heading">
                  <p className="eyebrow">Original source</p>
                  <h2 id="source-resume-heading" className="font-display mt-2 break-words text-2xl font-normal">{sourceResume?.filename || "Connected resume"}</h2>
                  {sourceReady ? (
                    <>
                      <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Your original {sourceResume?.source_format?.toUpperCase()} is retained. Tailoring updates existing text in this file, without adding a separate highlights section or rebuilding the resume.</p>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button asChild size="sm" variant="secondary"><Link href={`/resume/preview?resume=${item.resume_id}`}><FileText size={15} /> View original</Link></Button>
                        <Button size="sm" variant="ghost" onClick={() => downloadOriginal.mutate()} disabled={downloadOriginal.isPending}><Download size={15} /> Download original {sourceResume?.source_format?.toUpperCase()}</Button>
                      </div>
                    </>
                  ) : (
                    <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground" role="status">{sourceIssue}</p>
                  )}
                  {downloadOriginal.isError ? <p className="mt-3 text-sm text-coral" role="alert">{downloadOriginal.error instanceof Error ? downloadOriginal.error.message : "Could not download the original resume."}</p> : null}
                </section>
              ) : null}
              <section>
                <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                  <div><p className="eyebrow">Evidence Graph</p><h2 className="font-display mt-2 text-2xl font-normal">Approved facts for this resume</h2><p className="mt-2 text-sm text-muted-foreground">{approvedCount} approved evidence {approvedCount === 1 ? "item" : "items"}.</p></div>
                  <Button variant="secondary" onClick={() => importEvidence.mutate()} disabled={!item.resume_id || importEvidence.isPending}><RefreshCw size={15} /> Import from resume</Button>
                </div>
                {!item.resume_id ? <EmptyState icon={FilePlus2} title="Connect a resume first" description="Edit this opportunity and choose a parsed resume before building evidence." /> : null}
                {evidence.isLoading ? <div className="mt-6"><LoadingBlock rows={4} /></div> : null}
                {evidence.isError ? <p className="mt-4 text-sm text-coral" role="alert">Could not load evidence for this resume. Refresh evidence below to try again.</p> : null}
                {item.resume_id && evidence.isSuccess && !(evidence.data || []).length ? <EmptyState icon={ShieldCheck} title="No evidence imported" description="Import the resume sections, then approve only the facts you want HireWiz to reuse." action={<Button onClick={() => importEvidence.mutate()}><FilePlus2 size={16} /> Import evidence</Button>} /> : null}
                <div className="mt-6 divide-y divide-border border-t border-border">
                  {(evidence.data || []).map((entry) => (
                    <article key={entry.id} className="grid gap-4 py-5 sm:grid-cols-[1fr_auto]">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-foreground">{entry.title}</h3><StatusBadge tone={entry.approval_state === "approved" ? "teal" : entry.approval_state === "rejected" ? "coral" : "neutral"}>{entry.approval_state}</StatusBadge></div>
                        {editingEvidenceId === entry.id ? <div className="mt-3"><label className="sr-only" htmlFor={`evidence-${entry.id}`}>Edit {entry.title} evidence</label><textarea id={`evidence-${entry.id}`} className="field-control min-h-32 resize-y" value={evidenceDraft} onChange={(event) => setEvidenceDraft(event.target.value)} /><p className="mt-2 text-xs text-muted-foreground">Saving a factual edit returns this item to pending review.</p><div className="mt-3 flex gap-2"><Button size="sm" onClick={() => editEvidence.mutate({ id: entry.id, evidenceText: evidenceDraft.trim() })} disabled={evidenceDraft.trim().length < 2 || editEvidence.isPending}>Save edit</Button><Button size="sm" variant="ghost" onClick={() => { setEditingEvidenceId(null); setEvidenceDraft(""); }} disabled={editEvidence.isPending}>Cancel</Button></div></div> : <p className="mt-2 line-clamp-3 text-sm leading-6 text-muted-foreground">{entry.evidence_text}</p>}
                        {entry.skills.length ? <p className="mt-2 text-xs text-muted-foreground">{entry.skills.slice(0, 8).join(" · ")}</p> : null}
                      </div>
                      <div className="flex items-start gap-1"><Button size="icon" variant="ghost" onClick={() => { setEditingEvidenceId(entry.id); setEvidenceDraft(entry.evidence_text); }} aria-label="Edit evidence"><Pencil size={16} /></Button><Button size="icon" variant="ghost" onClick={() => approveEvidence.mutate({ id: entry.id, state: "approved" })} aria-label="Approve evidence"><Check size={17} /></Button><Button size="icon" variant="ghost" onClick={() => approveEvidence.mutate({ id: entry.id, state: "rejected" })} aria-label="Reject evidence"><X size={17} /></Button></div>
                    </article>
                  ))}
                </div>
              </section>
              <section aria-labelledby="resume-versions-heading" className="border-t border-border pt-8">
                <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
                  <div className="max-w-2xl">
                    <p className="eyebrow">Resume versions</p>
                    <h2 id="resume-versions-heading" className="font-display mt-2 text-2xl font-normal">Review changes before approval</h2>
                    <p className="mt-3 text-sm leading-6 text-muted-foreground">Compare the original wording with the proposed changes for this role. Each replacement links to approved evidence. Downloads keep the source file format and layout.</p>
                  </div>
                  <div className="min-w-0 space-y-3 xl:max-w-md">
                    <div className="flex flex-wrap gap-2">
                      <Button aria-describedby={tailoringIssue ? "resume-actions-status" : undefined} disabled={connectResume.isPending || !sourceReady || !evidence.isSuccess || approvedCount === 0 || tailoringIsRunning} onClick={() => startTailoring.mutate()}>
                        {tailoringIsRunning ? <LoaderCircle size={16} className="animate-spin" /> : <WandSparkles size={16} />}
                        Generate tailored version
                      </Button>
                      <Button variant="secondary" aria-describedby={tailoringIssue ? "resume-actions-status" : undefined} disabled={connectResume.isPending || !sourceReady || createVersion.isPending} onClick={() => createVersion.mutate()}><FilePlus2 size={16} /> Save source snapshot</Button>
                    </div>
                    {tailoringIssue ? <p id="resume-actions-status" className="text-xs leading-5 text-muted-foreground" role="status">{tailoringIssue}</p> : null}
                    {sourceIssue && sourceState !== "loading" ? <div className="flex flex-wrap gap-2">
                      {sourceCanRefresh ? <Button size="sm" variant="secondary" disabled={opportunity.isFetching || resumes.isFetching} onClick={refreshSourceDetails}><RefreshCw size={14} /> Refresh details</Button> : null}
                      {sourceState === "confirmed_absent" ? <Button asChild size="sm"><Link href={sourceUploadHref}><FilePlus2 size={14} /> Upload source again</Link></Button> : null}
                      <Button size="sm" variant="ghost" onClick={chooseResume}>Choose resume</Button>
                    </div> : null}
                    {sourceReady && evidence.isError ? <Button size="sm" variant="secondary" disabled={evidence.isFetching} onClick={() => void evidence.refetch()}><RefreshCw size={14} /> Refresh evidence</Button> : null}
                  </div>
                </div>
                <RunFeedback run={tailorRun.data} />
                {startTailoring.isError ? <p className="mt-3 text-sm text-coral" role="alert">{startTailoring.error instanceof Error ? startTailoring.error.message : "Could not start tailoring."}</p> : null}
                {tailored ? <p className="mt-4 border-l-2 border-primary pl-4 text-sm font-semibold text-primary" role="status">Version {tailored.version_number} created. Review its proposed changes below before approval.</p> : null}
                {createVersion.isError ? <p className="mt-3 text-sm text-coral" role="alert">{createVersion.error instanceof Error ? createVersion.error.message : "Could not save the source snapshot."}</p> : null}
                {downloadVersion.isError ? <p className="mt-3 text-sm text-coral" role="alert">{downloadVersion.error instanceof Error ? downloadVersion.error.message : "Could not download this resume version."}</p> : null}
                {updateVersion.isError ? <p className="mt-3 text-sm text-coral" role="alert">{updateVersion.error instanceof Error ? updateVersion.error.message : "Could not update version approval."}</p> : null}
                <div className="mt-6 space-y-5">
                  {item.resume_versions.map((version) => {
                    const content = getSourcePreservingContent(version.structured_content);
                    const versionResume = resumes.data?.resumes.find((resume) => resume.id === version.resume_id);
                    const nativeFormat = content && versionResume?.source_available && versionResume.source_format === content.source_format ? content.source_format : null;
                    const isUpdating = updateVersion.isPending && updateVersion.variables?.id === version.id;
                    const isDownloading = downloadVersion.isPending && downloadVersion.variables?.id === version.id;
                    return (
                      <article key={version.id} className="min-w-0 rounded-lg border border-border p-5 sm:p-6">
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                          <div className="min-w-0">
                            <h3 className="break-words text-base font-semibold text-foreground">{version.label}</h3>
                            <p className="mt-2 text-xs leading-5 text-muted-foreground">Version {version.version_number} · {version.evidence_ids.length} evidence links{content ? ` · ${content.source_format.toUpperCase()} source layout` : " · older export"}{version.submitted_at ? " · submitted" : ""}</p>
                          </div>
                          <div className="flex shrink-0 flex-wrap items-center gap-3">
                            <StatusBadge tone={version.approval_state === "approved" ? "teal" : version.approval_state === "rejected" ? "coral" : "neutral"}>{version.approval_state}</StatusBadge>
                            {nativeFormat === "pdf" ? <Button asChild size="sm" variant="ghost"><Link href={`/resume/preview?resume=${version.resume_id}&version=${encodeURIComponent(version.id)}`}><FileText size={14} /> Preview version</Link></Button> : null}
                            {nativeFormat ? <Button size="sm" variant="secondary" onClick={() => downloadVersion.mutate({ id: version.id, versionNumber: version.version_number, format: nativeFormat })} disabled={isDownloading}>{isDownloading ? <LoaderCircle size={14} className="animate-spin" /> : <Download size={14} />} {nativeFormat === "docx" && version.approval_state !== "approved" ? "Download draft DOCX" : `Download ${nativeFormat.toUpperCase()}`}</Button> : null}
                          </div>
                        </div>
                        {content ? (
                          <details className="mt-5 border-t border-border pt-4" open={version.id === tailored?.resume_version_id}>
                            <summary className="cursor-pointer text-sm font-semibold text-primary">{content.source_edits.length ? `Review ${content.source_edits.length} proposed ${content.source_edits.length === 1 ? "change" : "changes"}` : "Review source snapshot"}</summary>
                            <div className="mt-5 space-y-5">
                              {nativeFormat === "docx" ? <p className="text-sm leading-6 text-muted-foreground">Download the draft DOCX and review its text and layout in your document editor before approving this version.</p> : nativeFormat === "pdf" ? <p className="text-sm leading-6 text-muted-foreground">Use Preview version to check the exact PDF layout before approving.</p> : null}
                              {content.source_edits.map((edit, index) => (
                                <div key={edit.unit_id} className="min-w-0 rounded-lg bg-surface p-4 sm:p-5">
                                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                                    <h4 className="font-mono text-xs font-medium text-foreground">Change {String(index + 1).padStart(2, "0")}</h4>
                                    <span className="text-xs text-primary">{new Set(edit.evidence_ids).size} approved evidence {new Set(edit.evidence_ids).size === 1 ? "link" : "links"}</span>
                                  </div>
                                  {edit.reason ? <p className="mt-2 break-words text-xs leading-5 text-muted-foreground">{edit.reason}</p> : null}
                                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                                    <div className="min-w-0"><p className="data-label">Before</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">{edit.original_text}</p></div>
                                    <div className="min-w-0 border-t border-border pt-4 md:border-l md:border-t-0 md:pl-5 md:pt-0"><p className="data-label">After</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">{edit.replacement_text}</p></div>
                                  </div>
                                </div>
                              ))}
                              {!content.source_edits.length ? <p className="text-sm leading-6 text-muted-foreground">This snapshot keeps your original resume unchanged. View the original file before approving it.</p> : null}
                              {content.evidence_needed?.length ? <div className="border-l-2 border-border pl-4"><p className="data-label">Requirements needing more evidence</p><ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-muted-foreground">{content.evidence_needed.map((requirement, index) => <li key={index}>{requirement}</li>)}</ul></div> : null}
                              <Button asChild size="sm" variant="ghost"><Link href={`/resume/preview?resume=${version.resume_id}`}><FileText size={15} /> View original source</Link></Button>
                              {!nativeFormat ? <p className="text-sm leading-6 text-muted-foreground">{resumes.isLoading ? "Checking the original file before approval and download..." : resumes.isError ? "Source file details could not be loaded. Retry loading above before approval or download." : "The original source for this version is unavailable. Upload the source again and generate a new version before approval or download."}</p> : null}
                              <div className="border-t border-border pt-4">
                                {version.approval_state !== "approved" ? <label className="flex items-start gap-3 text-sm leading-6 text-foreground"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={Boolean(reviewedVersionIds[version.id])} onChange={(event) => { const checked = event.target.checked; setReviewedVersionIds((reviewed) => ({ ...reviewed, [version.id]: checked })); }} /> I have reviewed this version and its proposed changes.</label> : null}
                                <div className="mt-4 flex flex-wrap gap-2">
                                  <Button size="sm" onClick={() => updateVersion.mutate({ id: version.id, state: "approved" })} disabled={!nativeFormat || !reviewedVersionIds[version.id] || isUpdating || version.approval_state === "approved"}><Check size={14} /> {version.approval_state === "approved" ? "Approved" : "Approve version"}</Button>
                                  <Button size="sm" variant="ghost" onClick={() => updateVersion.mutate({ id: version.id, state: "rejected" })} disabled={isUpdating || version.approval_state === "rejected"}><X size={14} /> Reject version</Button>
                                </div>
                              </div>
                            </div>
                          </details>
                        ) : (
                          <div className="mt-5 border-t border-border pt-4">
                            <p className="text-sm leading-6 text-muted-foreground">This older version was rebuilt from extracted text. Generate a new version from the original source to preserve your resume format.</p>
                            <Button size="sm" variant="ghost" className="mt-3" onClick={() => updateVersion.mutate({ id: version.id, state: "rejected" })} disabled={isUpdating || version.approval_state === "rejected"}><X size={14} /> Reject version</Button>
                          </div>
                        )}
                      </article>
                    );
                  })}
                  {!item.resume_versions.length ? <p className="text-sm leading-6 text-muted-foreground">No version saved yet. Generate a tailored version or save your unchanged source snapshot.</p> : null}
                </div>
              </section>
            </div>
          ) : null}

          {tab === "learning" ? (
            <section>
              <div className="max-w-2xl"><p className="eyebrow">Skill ROI</p><h2 className="font-display mt-2 text-2xl font-normal">What is worth learning next</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Ranked across your active opportunities and approved evidence.</p></div>
              {skillRoi.isLoading ? <div className="mt-7"><LoadingBlock rows={5} /></div> : null}
              {!skillRoi.isLoading && !skillRoi.data?.items.length ? <EmptyState icon={BookOpenCheck} title="Skill ROI needs match data" description="Run matches for active opportunities to see which skill investment has the strongest return." /> : null}
              <div className="mt-7 divide-y divide-border border-y border-border">
                {(skillRoi.data?.items || []).map((entry, index) => (
                  <article key={entry.skill} className="grid gap-4 py-5 sm:grid-cols-[44px_1fr_auto] sm:items-center">
                    <span className="text-2xl font-semibold text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
                    <div><h3 className="font-semibold text-foreground">{entry.skill}</h3><p className="mt-1 text-sm text-muted-foreground">{entry.reason}</p></div>
                    <div className="sm:text-right"><p className="text-xl font-semibold text-primary">{Math.round(entry.score)}</p><p className="text-xs text-muted-foreground">~{entry.estimated_hours} hours</p></div>
                  </article>
                ))}
              </div>
            </section>
          ) : null}

          {tab === "interview" ? (
            <section aria-labelledby="interview-heading">
              <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
                <div className="max-w-2xl">
                  <p className="eyebrow">Role-specific preparation</p>
                  <h2 id="interview-heading" className="font-display mt-2 text-3xl font-normal">Interview questions</h2>
                  <p className="mt-2 max-w-prose text-sm leading-6 text-muted-foreground">Generate {interviewQuestionCount} practice questions for this role. Review the guidance, then build your answers from approved evidence.</p>
                </div>
                <Button className="min-h-11 shrink-0 self-start sm:self-auto" onClick={() => startInterview.mutate()} disabled={interviewIsRunning}>
                  {interviewIsRunning ? <LoaderCircle size={16} className="animate-spin" /> : <BrainCircuit size={16} />}
                  {interviewIsRunning ? "Generating questions" : interviewQuestions.length ? "Regenerate questions" : "Generate questions"}
                </Button>
              </div>
              {interviewQuestions.length ? (
                <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-border py-4 text-sm">
                  <span className="font-semibold text-foreground">{interviewQuestions.length} {interviewQuestions.length === 1 ? "question" : "questions"}</span>
                  {evidenceBackedCount ? <span className="inline-flex items-center gap-2 text-primary"><ShieldCheck size={16} aria-hidden="true" /> {evidenceBackedCount} evidence-backed</span> : null}
                  {interviewQuestions.length > evidenceBackedCount ? <span className="inline-flex items-center gap-2 text-muted-foreground"><CircleAlert size={16} aria-hidden="true" /> {interviewQuestions.length - evidenceBackedCount} {interviewQuestions.length - evidenceBackedCount === 1 ? "needs" : "need"} evidence</span> : null}
                </div>
              ) : null}
              <RunFeedback run={interviewRun.data} />
              {startInterview.isError ? <p className="mt-4 text-sm text-coral" role="alert">{startInterview.error instanceof Error ? startInterview.error.message : "Could not generate questions. Please try again."}</p> : null}
              {interviewResult.isLoading ? <div className="mt-6"><LoadingBlock rows={3} /></div> : null}
              {interviewResult.isError ? <div className="mt-4 flex flex-wrap items-center gap-3 text-sm" role="alert"><p className="text-coral">Could not load your interview questions.</p><Button variant="secondary" size="sm" onClick={() => void interviewResult.refetch()}>Retry loading</Button></div> : null}
              <div className="mt-6 space-y-4">
                {interviewQuestions.map((question, index) => {
                  const evidenceBacked = question.answer_state === "evidence_backed";
                  const sourceCount = question.evidence_ids?.length || 0;
                  return (
                    <article key={`${index}-${question.question}`} aria-labelledby={`interview-question-${index}`} className="rounded-xl border border-border bg-white p-5 transition-colors hover:border-accent sm:p-6">
                      <div className="flex items-start gap-3 sm:gap-4">
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center whitespace-nowrap rounded-full bg-surface font-mono text-sm font-medium tabular-nums text-primary" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                        <div className="min-w-0 flex-1">
                          <h3 id={`interview-question-${index}`} className="break-words text-base font-semibold leading-6 text-foreground sm:text-lg sm:leading-7">{question.question}</h3>
                          <StatusBadge className="mt-2" tone={evidenceBacked ? "teal" : "amber"}>{evidenceBacked ? `Evidence-backed · ${sourceCount} ${sourceCount === 1 ? "source" : "sources"}` : "Evidence needed"}</StatusBadge>
                        </div>
                      </div>
                      {question.answer ? (
                        <div className="mt-5 rounded-lg bg-surface/80 p-4 sm:ml-14 sm:p-5">
                          <p className="data-label">{evidenceBacked ? "Suggested talking points" : "Preparation guidance"}</p>
                          <p className="mt-2 max-w-prose whitespace-pre-line break-words text-sm leading-7 text-muted-foreground">{question.answer}</p>
                        </div>
                      ) : null}
                      {!evidenceBacked ? (
                        <div className="mt-4 flex flex-col items-start gap-3 sm:ml-14 sm:flex-row sm:items-center sm:justify-between">
                          <p className="max-w-prose text-xs leading-5 text-muted-foreground">Add or approve relevant evidence, then regenerate your questions.</p>
                          <Button variant="secondary" size="sm" className="shrink-0" onClick={reviewResumeEvidence}>Review resume evidence <ArrowRight size={14} aria-hidden="true" /></Button>
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
              {!interviewQuestions.length && !interviewIsRunning && !interviewResult.isLoading && !interviewResult.isError && (!interviewRun.data || interviewRun.data.status === "succeeded") ? (
                <EmptyState icon={MessageSquareText} title={questions ? "No questions returned" : "Prepare from the actual role"} description={questions ? "Try generating another set of questions for this opportunity." : "Generate questions from the saved job description, then review your evidence before practicing."} />
              ) : null}
            </section>
          ) : null}

          {tab === "outcome" ? (
            <section className="grid gap-10 lg:grid-cols-[1fr_360px]">
              <div className="max-w-2xl">
                <p className="eyebrow">Outcome learning</p>
                <h2 className="font-display mt-2 text-2xl font-normal">Close the loop</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  Record what happened so future Skill ROI and application decisions can learn from your own history.
                </p>
                {item.outcome ? (
                  <div className="mt-7 border-l-2 border-primary pl-5">
                    <p className="data-label">Recorded outcome</p>
                    <p className="mt-2 text-lg font-semibold text-foreground">{item.outcome.replaceAll("_", " ")}</p>
                    {item.outcome_notes ? <p className="mt-2 text-sm leading-6 text-muted-foreground">{item.outcome_notes}</p> : null}
                    {item.outcome_at ? <p className="mt-2 text-xs text-muted-foreground">{new Date(item.outcome_at).toLocaleString("en-IN")}</p> : null}
                  </div>
                ) : (
                  <EmptyState icon={Trophy} title="No final outcome recorded" description="You can update this later without losing the application timeline." />
                )}
              </div>
              <div className="border-t border-border pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
                <label className="grid gap-2 text-sm font-semibold text-foreground">
                  Final outcome
                  <select className="field-control" value={outcome} onChange={(event) => setOutcome(event.target.value as Outcome)}>
                    <option value="offer_accepted">Offer accepted</option>
                    <option value="offer_declined">Offer declined</option>
                    <option value="rejected">Rejected</option>
                    <option value="withdrawn">Withdrawn</option>
                  </select>
                </label>
                <label className="mt-4 grid gap-2 text-sm font-semibold text-foreground">
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
              <p className="eyebrow">History</p><h2 className="font-display mt-2 text-2xl font-normal">Application activity</h2>
              <ol className="mt-7 border-l border-border pl-6">
                {item.activity.map((event) => (
                  <li key={event.id} className="relative pb-7"><span className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-background bg-primary" /><p className="text-sm font-bold text-foreground">{event.event_type === "stage_changed" ? `${stageLabels[event.from_stage || "saved"]} to ${stageLabels[event.to_stage || "saved"]}` : event.event_type === "outcome_recorded" ? "Outcome recorded" : "Opportunity created"}</p>{event.note ? <p className="mt-1 text-sm text-muted-foreground">{event.note}</p> : null}<p className="mt-1 text-xs text-muted-foreground">{new Date(event.occurred_at).toLocaleString("en-IN")}</p></li>
                ))}
              </ol>
            </section>
          ) : null}
        </div>
      </div>
    </main>
  );
}

export default function OpportunityPage() {
  return <Suspense fallback={<main className="app-page"><div className="page-container"><LoadingBlock rows={7} /></div></main>}><OpportunityContent /></Suspense>;
}
