import type { components } from "./generated/api";
import { ApiError } from "./api";
import type { ResumeSourceFormat } from "./types";
import { directResumeSourceEnabled, fetchDirectResumeSource } from "./resumeSource";

export type ResumeSourceEdit = {
  unit_id: string;
  original_text: string;
  replacement_text: string;
  evidence_ids: string[];
  reason: string;
};

export type SourcePreservingResumeContent = {
  format_preservation: "source";
  source_format: ResumeSourceFormat;
  source_edits: ResumeSourceEdit[];
  evidence_needed?: string[];
  partial_tailoring?: boolean;
  omitted_edits?: number;
};

export function getSourcePreservingContent(content: Record<string, unknown>): SourcePreservingResumeContent | null {
  if (content.format_preservation !== "source" || (content.source_format !== "pdf" && content.source_format !== "docx" && content.source_format !== "tex" && content.source_format !== "texzip") || !Array.isArray(content.source_edits)) return null;
  const valid = content.source_edits.every((edit: unknown) => {
    if (!edit || typeof edit !== "object") return false;
    const item = edit as Record<string, unknown>;
    return typeof item.unit_id === "string" && typeof item.original_text === "string" && typeof item.replacement_text === "string" && typeof item.reason === "string" && Array.isArray(item.evidence_ids) && item.evidence_ids.every((id: unknown) => typeof id === "string");
  });
  const validNeeded = content.evidence_needed === undefined || (Array.isArray(content.evidence_needed) && content.evidence_needed.every((item: unknown) => typeof item === "string"));
  return valid && validNeeded ? content as SourcePreservingResumeContent : null;
}

async function fetchResumeBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`/api/backend${path}`, { cache: "no-store", signal });
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : null;
    throw new ApiError(typeof detail === "string" ? detail : "Could not load the resume file.", response.status, data);
  }
  return response.blob();
}

export async function fetchResumeSource(resumeId: number, signal?: AbortSignal, sourceFormat?: ResumeSourceFormat): Promise<Blob> {
  if (directResumeSourceEnabled() && (sourceFormat === undefined || sourceFormat === "pdf" || sourceFormat === "docx")) {
    const file = await fetchDirectResumeSource(resumeId, signal, sourceFormat);
    if (file) return file.blob;
  }
  return fetchResumeBlob(`/resume/${resumeId}/source`, signal);
}

export function fetchResumeVersionPdf(versionId: string, signal?: AbortSignal): Promise<Blob> {
  return fetchResumeBlob(`/v1/resume-versions/${encodeURIComponent(versionId)}/download?format=pdf`, signal);
}

export type Opportunity = components["schemas"]["OpportunityResponse"];
export type OpportunityDetail = components["schemas"]["OpportunityDetailResponse"];
export type OpportunityList = components["schemas"]["OpportunityListResponse"];
export type OpportunityMatch = components["schemas"]["OpportunityMatchResponse"];
export type ApplicationEvent = components["schemas"]["ApplicationEventResponse"];
export type EvidenceItem = components["schemas"]["EvidenceResponse"];
export type EvidenceImport = components["schemas"]["EvidenceImportResponse"];
export type ResumeVersion = components["schemas"]["ResumeVersionResponse"];
export type Reminder = components["schemas"]["ReminderResponse"];
export type CareerMemory = components["schemas"]["CareerMemoryResponse"];
export type SkillRoi = components["schemas"]["SkillRoiResponse"];
export type AnalysisRun = components["schemas"]["AnalysisRunResponse"];
export type AnalysisResult = components["schemas"]["AnalysisRunResultResponse"];

export const stages = [
  "saved",
  "evaluating",
  "preparing",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "withdrawn",
] as const;

export type OpportunityStage = (typeof stages)[number];

export const stageLabels: Record<string, string> = {
  saved: "Saved",
  evaluating: "Evaluating",
  preparing: "Preparing",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  archived: "Archived",
};

export const stageTone: Record<string, "neutral" | "teal" | "amber" | "coral"> = {
  saved: "neutral",
  evaluating: "teal",
  preparing: "amber",
  applied: "teal",
  interviewing: "amber",
  offer: "teal",
  rejected: "coral",
  withdrawn: "neutral",
  archived: "neutral",
};
