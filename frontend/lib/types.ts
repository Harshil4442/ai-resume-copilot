export type UserMeResponse = {
  id: number;
  email: string;
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

// Analytics
export type MatchHistoryItem = {
  // backend uses created_at + score
  created_at?: string;
  score?: number;

  // some older frontend versions used timestamp + match_score
  timestamp?: string;
  match_score?: number;

  // some very old versions used day + score
  day?: string;
};

export type AnalyticsSummary = {
  profile_completeness: number;
  average_match_score: number;
  resume_count: number;
  applications_count: number;
  match_history: MatchHistoryItem[];
};