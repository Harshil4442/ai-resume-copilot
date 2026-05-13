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

class JobMatchResponse(BaseModel):
    match_id: int
    match_score: float
    required_skills: List[str]
    missing_skills: List[str]
    weak_skills: List[str]
    fit_summary: str

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
