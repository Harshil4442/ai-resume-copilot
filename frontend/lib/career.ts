import type { components } from "./generated/api";

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
