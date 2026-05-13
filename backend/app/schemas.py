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
# Job matching
# -------------------------
class JobMatchRequest(BaseModel):
    resume_id: int
    job_title: str
    company: Optional[str] = None
    job_description: Union[str, List[str]]

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
