from datetime import datetime
from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# -------------------------
# Auth
# -------------------------
class AuthRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    accepted_terms: Literal[True]
    confirmed_age_18: Literal[True]

class AuthLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str

class AuthGoogleLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The backend derives identity from Google's signed claims. Never accept
    # an email address from the browser/NextAuth bridge as proof of identity.
    id_token: str = Field(min_length=100, max_length=4096)
    registration_consent: bool = False
    policy_version: Optional[str] = Field(default=None, max_length=32)

class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserMeResponse(BaseModel):
    id: int
    email: EmailStr
    tier: str
    ai_credits: int

class UserProfileBase(BaseModel):
    full_name: Optional[str] = None
    headline: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    target_role: Optional[str] = None
    preferred_job_type: Optional[str] = None
    preferred_location: Optional[str] = None
    years_experience: Optional[float] = None
    bio: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    education: Optional[str] = None
    certifications: Optional[str] = None

class UserProfileResponse(UserProfileBase):
    email: EmailStr
    profile_completeness: int
    missing_fields: List[str] = Field(default_factory=list)
    tier: str
    ai_credits: int
    premium_until: Optional[datetime] = None

class UserProfileUpdate(UserProfileBase):
    pass

# -------------------------
# Resume parsing
# -------------------------
class ContactInfo(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None

class ResumeParseResponse(BaseModel):
    resume_id: int
    skills: List[str]
    experience_years: float
    sections: Dict[str, str]
    contact_info: ContactInfo

# -------------------------
# Resume list (for dropdown)
# -------------------------
class ResumeListItem(BaseModel):
    id: int
    filename: str
    created_at: datetime

    class Config:
        from_attributes = True

class ResumeListResponse(BaseModel):
    resumes: List[ResumeListItem]

# -------------------------
# Job matching
# -------------------------
class JobMatchRequest(BaseModel):
    resume_id: int
    job_title: str
    company: Optional[str] = None
    job_description: Union[str, List[str]]

class PartialMatch(BaseModel):
    skill:    str
    coverage: int
    via:      str

class DimensionScore(BaseModel):
    name:     str
    score:    float
    feedback: str

class JobMatchResponse(BaseModel):
    match_id:                int
    match_score:             float
    grade:                   str
    required_skills:         List[str]
    full_matches:            List[str]
    partial_matches:         List[PartialMatch]
    true_gaps:               List[str]
    skill_verification_rate: int
    dimensions:              List[DimensionScore]
    fit_summary:             str
    improvement_tips:        List[str]

class JobMatchHistoryItem(BaseModel):
    match_id: int
    job_title: str
    company: str
    match_score: float
    created_at: datetime

    class Config:
        from_attributes = True

class JobMatchHistoryResponse(BaseModel):
    matches: List[JobMatchHistoryItem]

# -------------------------
# Match-specific learning strategy
# -------------------------
class LearningStrategyRequest(BaseModel):
    match_id: int

class LearningResource(BaseModel):
    title: str
    platform: str
    url: Optional[str] = None
    skill: str
    level: Optional[str] = None

class MissingHiringSignal(BaseModel):
    signal: str
    why_it_matters: str
    severity: str = "medium"

class LearningPriority(BaseModel):
    skill: str
    priority: str = "medium"
    current_status: str = "gap"
    reason: str
    expected_outcome: str
    resources: List[LearningResource] = Field(default_factory=list)

class ProjectRecommendation(BaseModel):
    title: str
    covers_gaps: List[str]
    description: str
    implementation_steps: List[str]
    resume_bullets: List[str]
    interview_talking_points: List[str]

class LearningTimelineItem(BaseModel):
    phase: str
    focus: str
    deliverable: str

class LearningStrategyResponse(BaseModel):
    match_id: int
    job_title: str
    company: str
    current_score: float
    readiness_summary: str
    missing_hiring_signals: List[MissingHiringSignal]
    learning_priorities: List[LearningPriority]
    project_recommendations: List[ProjectRecommendation]
    timeline: List[LearningTimelineItem]
    generated_by: str = "llm"

# -------------------------
# Stateless Ask AI / RAG
# -------------------------
class RagMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class RagAskRequest(BaseModel):
    job_match_id: int
    question: str = Field(min_length=2, max_length=1000)
    resume_id: Optional[int] = None
    recent_messages: List[RagMessage] = Field(default_factory=list)

class RagAskResponse(BaseModel):
    answer: str
    confidence: Literal["high", "medium", "low"] = "medium"
    suggested_followups: List[str] = Field(default_factory=list)

# -------------------------
# Market skill trends
# -------------------------
class MarketAnalyzeRequest(BaseModel):
    target_role: str = Field(min_length=2, max_length=120)
    location: Optional[str] = Field(default="", max_length=120)
    country_code: Optional[str] = Field(default="", max_length=2)
    experience_level: Optional[str] = Field(default="", max_length=40)
    remote: Optional[bool] = None
    resume_id: Optional[int] = None
    max_results: int = Field(default=50, ge=5, le=100)
    posted_within_days: int = Field(default=30, ge=1, le=365)

class MarketTopSkill(BaseModel):
    skill: str
    count: int
    percentage: float
    category: str
    importance: Literal["critical", "high", "medium", "low"]

class MarketCategorySkill(BaseModel):
    skill: str
    count: int
    percentage: float

class MarketSkillCategory(BaseModel):
    category: str
    skills: List[MarketCategorySkill]

class MarketResumeGap(BaseModel):
    skill: str
    market_demand_percentage: float
    resume_status: Literal["proven", "claimed", "missing"]
    priority: Literal["critical", "high", "medium", "low"]
    reason: str

class MarketProjectRecommendation(BaseModel):
    title: str
    skills_covered: List[str]
    difficulty: Literal["beginner", "intermediate", "advanced"]
    description: str
    resume_bullets: List[str]

class MarketLearningPriority(BaseModel):
    skill: str
    priority: Literal["critical", "high", "medium", "low"]
    why: str

class MarketSampleJob(BaseModel):
    title: str
    company: str
    location: str
    posted_at: str
    url: str
    source: str

class MarketAnalyzeResponse(BaseModel):
    target_role: str
    location: str = ""
    country_code: str = ""
    experience_level: str = ""
    remote: Optional[bool] = None
    source_provider: str
    from_cache: bool = False
    sample_size: int
    confidence: Literal["high", "medium", "low"]
    top_skills: List[MarketTopSkill]
    skill_categories: List[MarketSkillCategory]
    resume_gap_analysis: List[MarketResumeGap]
    recommended_projects: List[MarketProjectRecommendation]
    learning_priorities: List[MarketLearningPriority]
    summary: str
    warnings: List[str] = Field(default_factory=list)
    sample_jobs: List[MarketSampleJob] = Field(default_factory=list)

# -------------------------
# LLM helpers
# -------------------------
class RewriteBulletsRequest(BaseModel):
    resume_id: int
    job_description: str
    tone: str = "concise"

class RewriteBulletsResponse(BaseModel):
    rewritten_bullets: List[str]
    summary: str

class InterviewQuestionsRequest(BaseModel):
    job_title: str
    job_description: str

class InterviewQuestionsResponse(BaseModel):
    questions: List[Dict[str, str]]  # {question, answer}

# -------------------------
# Resume Tailoring
# -------------------------
class ResumeTailorRequest(BaseModel):
    template_type: str = Field(default="ats", description="ats, executive, technical, or creative")

class ResumeTailorResponse(BaseModel):
    tailored_resume_markdown: str
    pdf_base64: str | None = None
