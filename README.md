# AI Resume Parser & Job Match Assistant

A full-stack web app that lets users **sign up / log in**, **upload & parse resumes (PDF)**, **store data per user**, **match resumes to job descriptions**, and **get learning recommendations** based on skill gaps — with a **dashboard** showing analytics like resume count and match trends.

Designed to deploy as:
- **Frontend** on **Vercel (Next.js)**
- **Backend API** on **Google Cloud Run (FastAPI)**
- **Database** on **PostgreSQL** (Render/Neon/Supabase/any hosted Postgres)

---

## Features

### ✅ Authentication (per-user data)
- User **register / login**
- JWT-based auth
- Every resume, match, and dashboard stat is **scoped to the logged-in user**

### ✅ Resume parsing (PDF)
- Upload resume as PDF
- Extract:
  - raw text
  - sections
  - skills
  - estimated experience years
- Stores parsed resume in database under the authenticated user

### ✅ Job matching
- Paste a job description + select a resume ID
- Extracts required skills from the JD
- Computes match score and missing skills
- Saves match history per user

> Matching runs in a **fast overlap mode** by default to be stable on free tiers.

### ✅ Learning / Gap recommendations
- Runs gap analysis: current skills vs required skills for a role
- Returns recommended learning resources (courses/links) from a curated dataset
- Shows skill gaps clearly

### ✅ Dashboard analytics
- Profile completeness heuristic (based on unique skills extracted)
- Average match score
- Resume count / application count
- Match history trend chart

---

## Tech Stack

### Frontend
- **Next.js 14 (App Router)**
- **TypeScript**
- **Tailwind CSS**
- Charting: **recharts**
- API helper layer for auth + fetch + proxy

### Backend
- **FastAPI**
- **SQLAlchemy** ORM
- **PostgreSQL** via `psycopg2-binary`
- Auth: **JWT** (`python-jose`) + password hashing (`passlib`)
- Resume parsing: **pdfplumber**
- NLP / skill extraction: **spaCy** + keyword heuristics
- Optional semantic matching: **sentence-transformers** (can be disabled for low-memory)

### Deployment
- **Google Cloud Run** (backend)
- **Vercel** (frontend)
- Postgres hosted anywhere

---

## Project Structure (typical)

backend/             # FastAPI app
  app/
    main.py
    database.py
    models.py
    routers/
    services/
frontend/            # Next.js app
  app/
  lib/
  components/

---

# Run Locally

## 1) Clone repo
git clone <your-repo-url>
cd <your-repo-folder>

---

# Backend (FastAPI)

## 2) Create venv + install deps
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

## 3) Install spaCy model (required)
python -m spacy download en_core_web_sm

## 4) Create backend/.env
Create file: backend/.env

DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
JWT_SECRET=change-me-to-a-long-random-secret

# Optional
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

# Recommended for free/low memory tiers
USE_SENTENCE_TRANSFORMER=0
HF_HUB_DISABLE_XET=1

# Optional: Only if you use any LLM endpoints
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-REPLACE_ME

## 5) Start backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Backend:
- http://localhost:8000
- Swagger docs: http://localhost:8000/docs

---

# Frontend (Next.js)

## 6) Install deps
cd ../frontend
npm install

## 7) Create frontend/.env.local
Create file: frontend/.env.local

# Local dev: frontend calls backend directly
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api

## 8) Start frontend
npm run dev

Frontend:
- http://localhost:3000

---

# Deploy

## Backend on Google Cloud Run

### A) Set runtime env vars via GUI
Cloud Run → Services → ai-resume-parser → Edit & Deploy New Revision → Variables & Secrets

Add:
DATABASE_URL
JWT_SECRET
USE_SENTENCE_TRANSFORMER=0
HF_HUB_DISABLE_XET=1
LLM_API_BASE (optional)
LLM_MODEL (optional)
LLM_API_KEY (optional)

### B) Or set runtime env vars via CLI
gcloud run services update ai-resume-parser \
  --region us-central1 \
  --set-env-vars USE_SENTENCE_TRANSFORMER=0,HF_HUB_DISABLE_XET=1 \
  --set-env-vars DATABASE_URL="postgresql://..." \
  --set-env-vars JWT_SECRET="your-secret" \
  --set-env-vars LLM_API_BASE="https://api.openai.com/v1",LLM_MODEL="gpt-4o-mini"

### C) Full deploy from local folder (source deploy)
# Run inside backend/ (or repo root if Dockerfile is at root—depends on your setup)
gcloud run deploy ai-resume-parser \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars USE_SENTENCE_TRANSFORMER=0,HF_HUB_DISABLE_XET=1 \
  --set-env-vars DATABASE_URL="postgresql://..." \
  --set-env-vars JWT_SECRET="your-secret"

After deploy you’ll get a Cloud Run URL like:
https://ai-resume-parser-xxxxx.us-central1.run.app

Your API base becomes:
https://ai-resume-parser-xxxxx.us-central1.run.app/api

---

## Frontend on Vercel

### A) Set env var in Vercel GUI
Vercel → Project → Settings → Environment Variables

Add:
NEXT_PUBLIC_API_BASE_URL = https://YOUR_CLOUD_RUN_URL/api

Example:
NEXT_PUBLIC_API_BASE_URL=https://ai-resume-parser-xxxxx.us-central1.run.app/api

Apply to:
- Production
- Preview
- Development (optional but recommended)

Redeploy after changing env vars.

### B) Ensure Next.js rewrite is safe (important)
If you use next.config.js rewrites, DO NOT build a destination from an undefined env.
The simplest approach is to not depend on rewrites at all and use NEXT_PUBLIC_API_BASE_URL.

Recommended: In frontend code, always call API via:
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

And set NEXT_PUBLIC_API_BASE_URL on Vercel.

---

# Troubleshooting

## 1) 401 Unauthorized on dashboard / api calls
You are not logged in or token is missing/expired.
- Login again
- Check browser localStorage contains "access_token"
- Confirm frontend sends Authorization header (Bearer token)

## 2) 500 Internal Server Error
Check Cloud Run logs:
gcloud run services logs read ai-resume-parser --region us-central1 --limit 200

## 3) Avoid heavy ML downloads / memory issues
Keep:
USE_SENTENCE_TRANSFORMER=0
HF_HUB_DISABLE_XET=1

## 4) Database connection errors
Check DATABASE_URL is correct and publicly reachable from Cloud Run.
Render/Neon/Supabase DBs need correct host/user/password/dbname and SSL settings if required.

---

# Environment Variables Summary (with examples)

## Backend (Cloud Run / backend/.env)
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
JWT_SECRET=6547d5f9d754sxyugtfuxy8d967ufg7h
USE_SENTENCE_TRANSFORMER=0
HF_HUB_DISABLE_XET=1
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-REPLACE_ME

## Frontend (Vercel / frontend/.env.local)
NEXT_PUBLIC_API_BASE_URL=https://YOUR_CLOUD_RUN_URL/api
