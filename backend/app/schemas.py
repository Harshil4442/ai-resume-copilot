from datetime import datetime
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, EmailStr, Field

# -------------------------
# Auth
# -------------------------
class AuthRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)

class AuthLoginRequest(BaseModel):
    email: EmailStr
    password: str

class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserMeResponse(BaseModel):
    id: int
    email: EmailStr

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
