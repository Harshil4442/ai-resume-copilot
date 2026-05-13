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

    skills = Column(JSON, default=list)          # list[str]
    experience_years = Column(Float, default=0.0)
    sections = Column(JSON, default=dict)        # dict section_name -> text
    contact_info = Column(JSON, default=dict)    # {name, email, phone, linkedin, github}

    user = relationship("User", back_populates="resumes")

class JobMatch(Base):
    __tablename__ = "job_matches"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    resume_id = Column(Integer, ForeignKey("resumes.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    job_title = Column(String, default="")
    company = Column(String, default="")
    job_description = Column(Text, default="")

    required_skills = Column(JSON, default=list)
    match_score = Column(Float, default=0.0)
    missing_skills = Column(JSON, default=list)
    weak_skills = Column(JSON, default=list)

    user = relationship("User", back_populates="job_matches")
    resume = relationship("Resume")
