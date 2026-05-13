from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, Float, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String, default="")

    resumes = relationship("Resume", back_populates="user")
    job_matches = relationship("JobMatch", back_populates="user")

class Resume(Base):
    __tablename__ = "resumes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    original_filename = Column(String, default="")
    raw_text = Column(Text, default="")

    skills = Column(JSON, default=list)
    experience_years = Column(Float, default=0.0)
    sections = Column(JSON, default=dict)
    contact_info = Column(JSON, default=dict)

    user = relationship("User", back_populates="resumes")

class JobMatch(Base):
    __tablename__ = "job_matches"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    resume_id = Column(Integer, ForeignKey("resumes.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    job_title        = Column(String, default="")
    company          = Column(String, default="")
    job_description  = Column(Text, default="")

    required_skills  = Column(JSON, default=list)
    full_matches     = Column(JSON, default=list)  # direct + full coverage skills
    partial_matches  = Column(JSON, default=list)  # [{skill, coverage, via}]
    true_gaps        = Column(JSON, default=list)  # no meaningful coverage
    match_score      = Column(Float, default=0.0)
    fit_summary           = Column(Text, default="")
    dimension_scores      = Column(JSON, default=list)  # [{name,score,feedback}]
    skill_verification_rate = Column(Float, default=0.0)
    improvement_tips      = Column(JSON, default=list)

    user   = relationship("User", back_populates="job_matches")
    resume = relationship("Resume")


class SkillCoverage(Base):
    """
    Persistent DB cache for LLM-computed pairwise skill coverage weights.
    Written once per unique (skill_from, skill_to) pair, read on every subsequent request.
    """
    __tablename__ = "skill_coverage"

    skill_from = Column(String(120), primary_key=True)
    skill_to   = Column(String(120), primary_key=True)
    weight     = Column(Float, nullable=False)   # 0.0 to 1.0
    source     = Column(String(20), default="llm")
    created_at = Column(DateTime, default=datetime.utcnow)
