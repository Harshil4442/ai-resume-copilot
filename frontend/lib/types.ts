export type UserMeResponse = {
  id: number;
  email: string;
};

export type UserProfile = {
  email: string;
  profile_completeness: number;
  missing_fields: string[];
  full_name?: string | null;
  headline?: string | null;
  phone?: string | null;
  location?: string | null;
  linkedin?: string | null;
  github?: string | null;
  portfolio?: string | null;
  target_role?: string | null;
  preferred_job_type?: string | null;
  preferred_location?: string | null;
  years_experience?: number | null;
  bio?: string | null;
  skills: string[];
  education?: string | null;
  certifications?: string | null;
};

export type AuthTokenResponse = {
  access_token: string;
  token_type: "bearer";
};

export type ResumeParseResponse = {
  resume_id: number;
  skills: string[];
  experience_years: number;
  sections: Record<string, any>;
};

export type RecommendedCourse = {
  title: string;
  platform: string;
  skill: string;
  url?: string;
  level?: string;
};

export type GapAnalysisResponse = {
  current_skills?: string[];
  skill_gaps: string[];
  recommended_courses: RecommendedCourse[]; // ✅ add this
  [key: string]: any;
};

export type JobMatchResponse = {
  match_id: number;
  match_score: number;
  required_skills: string[];
  missing_skills: string[];
};

export type MatchHistoryItem = {
  timestamp: string;
  match_score: number;
  job_title?: string;
  company?: string;
  match_id?: number;
};

export type DashboardMatchCard = {
  match_id: number;
  job_title: string;
  company: string;
  match_score: number;
  created_at?: string | null;
};

export type AnalyticsSummary = {
  profile_completeness: number;
  average_match_score: number;
  resume_count: number;
  applications_count: number;
  match_history: MatchHistoryItem[];
  profile_health?: {
    score: number;
    missing_items: string[];
  };
  resume_quality?: {
    latest_resume_id?: number | null;
    latest_resume_filename: string;
    latest_resume_date?: string | null;
    total_unique_skills: number;
    evidenced_skills: number;
    claimed_only_skills: number;
    verification_rate: number;
    quantified_achievements: number;
    missing_sections: string[];
  };
  match_overview?: {
    best_match?: DashboardMatchCard | null;
    weakest_match?: DashboardMatchCard | null;
    latest_match?: DashboardMatchCard | null;
  };
  activity_summary?: {
    resumes_parsed: number;
    job_matches_run: number;
    last_activity_at?: string | null;
  };
  recurring_gaps?: Array<{ skill: string; count: number }>;
};

export type JobMatchHistoryItem = {
  match_id: number;
  job_title: string;
  company: string;
  match_score: number;
  created_at: string;
};

export type LearningResource = {
  title: string;
  platform: string;
  url?: string;
  skill: string;
  level?: string;
};

export type MissingHiringSignal = {
  signal: string;
  why_it_matters: string;
  severity: "high" | "medium" | "low" | string;
};

export type LearningPriority = {
  skill: string;
  priority: "high" | "medium" | "low" | string;
  current_status: string;
  reason: string;
  expected_outcome: string;
  resources: LearningResource[];
};

export type ProjectRecommendation = {
  title: string;
  covers_gaps: string[];
  description: string;
  implementation_steps: string[];
  resume_bullets: string[];
  interview_talking_points: string[];
};

export type LearningTimelineItem = {
  phase: string;
  focus: string;
  deliverable: string;
};

export type LearningStrategyResponse = {
  match_id: number;
  job_title: string;
  company: string;
  current_score: number;
  readiness_summary: string;
  missing_hiring_signals: MissingHiringSignal[];
  learning_priorities: LearningPriority[];
  project_recommendations: ProjectRecommendation[];
  timeline: LearningTimelineItem[];
  generated_by: string;
};
