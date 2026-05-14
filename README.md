# AI Resume CoPilot

AI Resume CoPilot is a full-stack career intelligence app that helps job seekers turn a resume into measurable career signals. Users can create a profile, upload and parse resumes, match resumes against jobs, ask grounded AI questions about a match, generate learning strategies, and analyze live job-market skill trends.

The project is designed for a split production deployment:

- Frontend: Next.js on Vercel
- Backend: FastAPI on Google Cloud Run
- Database: PostgreSQL on Render or any hosted Postgres provider
- LLM: Groq or any OpenAI-compatible chat-completions API
- Optional cache: Redis for market provider responses

---

## Current Features

### Premium Web UI

- Modern Apple/Lucien-inspired product UI.
- Responsive dashboard, resume, jobs, market trends, learning, profile, login, and register pages.
- Tailwind-based visual system with premium cards, product-stage sections, glass navigation, smooth hover states, and clean typography.

### Authentication And Profile

- User registration and login.
- JWT authentication with bearer tokens.
- Per-user data isolation for resumes, job matches, dashboard data, and profile data.
- Editable profile page with mostly optional fields:
  - name, headline, phone, location
  - LinkedIn, GitHub, portfolio
  - target role, preferred job type, preferred location
  - years of experience, bio, skills, education, certifications
- Profile completeness and missing-field guidance.

### Resume Parsing

- Upload and parse PDF resumes.
- Upload and parse DOCX resumes.
- Extracts:
  - raw resume text
  - contact info
  - sections
  - skills
  - estimated experience years
- Uses `pdfplumber`, `python-docx`, spaCy, fuzzy section detection, regex extraction, and heuristic fallback logic.

### Job Match Intelligence

- Select a parsed resume and paste a job description.
- Extract required skills from the job description.
- Compare resume skills, experience, projects, and sections against the target job.
- Produces:
  - match score
  - grade
  - required skills
  - full matches
  - partial matches
  - true gaps
  - skill verification rate
  - recruiter-style dimension scores
  - fit summary
  - improvement tips
- Saves match history per user.

### Stateless Ask AI For Job Matches

- Adds an Ask AI panel on the job match page.
- User can ask questions about a selected resume and job match.
- Backend builds temporary in-memory evidence chunks from:
  - resume sections
  - resume skills
  - experience and project evidence
  - job description
  - required skills
  - full matches, partial matches, true gaps
  - dimension scores
  - fit summary
  - improvement tips
- Uses lightweight TF-IDF/rule-based retrieval.
- Sends only top-ranked context to the LLM.
- Returns:
  - grounded answer
  - confidence
  - suggested follow-up questions
- No chat history is stored.
- No chunks are stored.
- No embeddings or vector database are used for this feature.

### Learning Recommendations

- Learning recommendations are generated for a specific job match from match history.
- The system acts like a senior hiring manager and suggests:
  - missing hiring signals
  - prioritized skills to improve
  - project ideas that cover the person's gaps
  - implementation steps
  - resume bullets
  - interview talking points
  - timeline items
- Includes deterministic fallback behavior if the LLM is unavailable.
- Can attach curated learning resources from `backend/resources/courses.json`.

### Job Market Skill Trend Analyzer

- Searches live job-provider APIs for a target role and market.
- Current provider order:
  - TheirStack
  - Adzuna
  - Jooble
- Supports provider fallback when multiple APIs are configured.
- Extracts repeated skills from sampled job postings.
- Counts each skill at most once per job.
- Normalizes aliases and groups skills into categories.
- Returns:
  - top demanded skills
  - skill percentages
  - skill categories
  - resume gap analysis
  - high-priority market gaps
  - project recommendations
  - resume bullet ideas
  - learning priorities
  - warnings
  - sample jobs used
- Optional Redis caching reduces repeated provider calls.

### Dashboard Analytics

- Profile completeness.
- Average match score.
- Resume count.
- Latest activity.
- Resume quality summary.
- Match performance summary.
- Match score trend chart.
- Recurring gap patterns.
- Quick actions.

### LLM Utilities

- Resume bullet rewriting.
- Interview question generation.
- LLM-assisted skill extraction and fit summaries where available.
- OpenAI-compatible client, so Groq can be used by setting `LLM_API_BASE`, `LLM_MODEL`, and `LLM_API_KEY`.

---

## Tech Stack

### Frontend

- Next.js 14 App Router
- React 18
- TypeScript
- Tailwind CSS
- Recharts
- Vercel deployment

### Backend

- FastAPI
- SQLAlchemy
- PostgreSQL or SQLite
- JWT auth with `python-jose`
- Password hashing with `passlib`
- `pdfplumber` for PDF parsing
- `python-docx` for DOCX parsing
- spaCy for NLP support
- RapidFuzz for fuzzy matching
- Redis for optional market response caching
- Pytest for backend tests

### AI And Market Data

- Groq or any OpenAI-compatible chat API
- TheirStack job search API
- Adzuna job search API
- Jooble job search API
- Deterministic fallbacks for core analysis where possible

---

## Repository Structure

```text
.
|-- backend/
|   |-- app/
|   |   |-- main.py
|   |   |-- database.py
|   |   |-- models.py
|   |   |-- schemas.py
|   |   |-- security.py
|   |   |-- routers/
|   |   |   |-- analytics.py
|   |   |   |-- auth.py
|   |   |   |-- jobs.py
|   |   |   |-- llm.py
|   |   |   |-- market.py
|   |   |   |-- rag.py
|   |   |   |-- recommendations.py
|   |   |   `-- resume.py
|   |   `-- services/
|   |       |-- llm_client.py
|   |       |-- matching.py
|   |       |-- parsing.py
|   |       |-- recommender.py
|   |       |-- market/
|   |       `-- rag/
|   |-- resources/
|   |   `-- courses.json
|   |-- Dockerfile
|   `-- requirements.txt
|-- frontend/
|   |-- app/
|   |   |-- dashboard/
|   |   |-- jobs/
|   |   |-- learning/
|   |   |-- login/
|   |   |-- market/
|   |   |-- profile/
|   |   |-- register/
|   |   |-- resume/
|   |   |-- globals.css
|   |   |-- layout.tsx
|   |   `-- page.tsx
|   |-- components/
|   |-- lib/
|   |-- package.json
|   `-- tailwind.config.ts
|-- tests/
|   `-- test_market_analyzer.py
|-- .github/workflows/ci.yml
|-- cloudbuild.yaml
|-- Dockerfile
|-- render.yaml
|-- start.sh
|-- vercel.json
`-- README.md
```

---

## Backend API Overview

All backend routes are mounted under `/api`.

| Area | Method | Route | Purpose |
| --- | --- | --- | --- |
| Health | GET | `/api/health` | Health check |
| Auth | POST | `/api/auth/register` | Create account |
| Auth | POST | `/api/auth/login` | Login and receive JWT |
| Auth | GET | `/api/auth/me` | Current user |
| Profile | GET | `/api/auth/profile` | Get editable profile |
| Profile | PUT | `/api/auth/profile` | Update profile |
| Resume | GET | `/api/resume/list` | List user's parsed resumes |
| Resume | POST | `/api/resume/parse` | Upload and parse resume |
| Jobs | POST | `/api/jobs/match` | Analyze resume against JD |
| Jobs | GET | `/api/jobs/matches` | Job match history |
| Ask AI | POST | `/api/rag/ask` | Stateless match Q&A |
| Learning | POST | `/api/recommendations/gaps` | Legacy gap recommendations |
| Learning | POST | `/api/recommendations/match_strategy` | Match-specific learning plan |
| Market | POST | `/api/market/analyze` | Live skill trend analysis |
| LLM | POST | `/api/llm/rewrite_bullets` | Rewrite resume bullets |
| LLM | POST | `/api/llm/interview_questions` | Generate interview questions |
| Analytics | GET | `/api/analytics/summary` | Dashboard summary |

Swagger docs are available locally at:

```text
http://localhost:8000/docs
```

---

## Database

The app currently uses SQLAlchemy models and creates tables automatically on backend startup with `Base.metadata.create_all`.

Current tables:

- `users`
- `user_profiles`
- `resumes`
- `job_matches`
- `skill_coverage`

For local development, the backend defaults to SQLite if `DATABASE_URL` is not set:

```text
sqlite:///./app.db
```

For production, use PostgreSQL:

```text
postgresql://USER:PASSWORD@HOST:PORT/DBNAME
```

No Alembic migrations are currently configured.

---

## Local Development

### Prerequisites

- Python 3.12 recommended
- Node.js 20 recommended
- npm
- PostgreSQL optional for local development
- Redis optional for market caching

### 1. Clone The Repo

```bash
git clone <your-repo-url>
cd ai-resume-copilot
```

### 2. Configure Backend Environment

Create `backend/.env`.

For quick local SQLite development:

```bash
DATABASE_URL=sqlite:///./app.db
JWT_SECRET=change-this-local-secret
FRONTEND_ORIGINS=http://localhost:3000
```

For local development with Groq:

```bash
LLM_API_BASE=https://api.groq.com/openai/v1
LLM_MODEL=YOUR_GROQ_MODEL
LLM_API_KEY=YOUR_GROQ_API_KEY
```

For live market trends:

```bash
THEIRSTACK_API_KEY=YOUR_THEIRSTACK_API_KEY
ADZUNA_APP_ID=YOUR_ADZUNA_APP_ID
ADZUNA_APP_KEY=YOUR_ADZUNA_APP_KEY
JOOBLE_API_KEY=YOUR_JOOBLE_API_KEY
```

For optional Redis cache:

```bash
REDIS_URL=rediss://default:YOUR_REDIS_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT
MARKET_CACHE_TTL_SECONDS=21600
```

You only need one configured market provider for `/api/market/analyze` to return live data. Multiple providers improve fallback coverage.

### 3. Run Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download en_core_web_sm
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend URLs:

```text
API: http://localhost:8000
Docs: http://localhost:8000/docs
Health: http://localhost:8000/api/health
```

Note: the app falls back to `spacy.blank("en")` if the spaCy model is missing, but installing `en_core_web_sm` is recommended for better local parsing.

### 4. Configure Frontend Environment

Create `frontend/.env.local`.

Option A: call backend directly:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api
```

Option B: use Next.js same-origin rewrite:

```bash
NEXT_PUBLIC_API_BASE_URL=/api
BACKEND_URL=http://localhost:8000
```

### 5. Run Frontend

```bash
cd ../frontend
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:3000
```

---

## Environment Variables

### Backend

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | Production yes | Database connection string. Defaults to local SQLite if missing. |
| `JWT_SECRET` | Production yes | Secret used to sign JWT access tokens. |
| `JWT_ALGORITHM` | No | Defaults to `HS256`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Defaults to `10080`. |
| `FRONTEND_ORIGINS` | Recommended | Comma-separated allowed frontend origins or `*`. |
| `LLM_API_BASE` | For LLM features | OpenAI-compatible API base. Groq uses `https://api.groq.com/openai/v1`. |
| `LLM_MODEL` | For LLM features | Chat model name. |
| `LLM_API_KEY` | For LLM features | LLM provider API key. |
| `THEIRSTACK_API_KEY` | For market provider | TheirStack API key. |
| `THEIRSTACK_API_URL` | No | Override TheirStack API URL. |
| `ADZUNA_APP_ID` | For Adzuna fallback | Adzuna app id. |
| `ADZUNA_APP_KEY` | For Adzuna fallback | Adzuna app key. |
| `ADZUNA_API_URL` | No | Override Adzuna API base. |
| `JOOBLE_API_KEY` | For Jooble fallback | Jooble API key. |
| `JOOBLE_API_URL` | No | Override Jooble API base. |
| `REDIS_URL` | Optional | Redis connection string for market cache. |
| `MARKET_CACHE_TTL_SECONDS` | Optional | Market cache TTL. Defaults to `21600`. |

### Frontend

| Variable | Required | Description |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | Recommended | API base used by frontend fetch helpers. Example: `https://YOUR_CLOUD_RUN_URL/api`. |
| `BACKEND_URL` | Optional | Backend base URL used by Next.js rewrites when frontend calls `/api`. Do not include `/api`. |

---

## Production Deployment

### Backend On Google Cloud Run

The backend can be deployed from `backend/` using `backend/Dockerfile`, or through the root `cloudbuild.yaml` if you are using the existing Cloud Build flow.

Recommended Cloud Run environment variables:

```bash
DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
JWT_SECRET="YOUR_LONG_RANDOM_SECRET"
FRONTEND_ORIGINS="https://YOUR_VERCEL_DOMAIN"

LLM_API_BASE="https://api.groq.com/openai/v1"
LLM_MODEL="YOUR_GROQ_MODEL"
LLM_API_KEY="YOUR_GROQ_API_KEY"

THEIRSTACK_API_KEY="YOUR_THEIRSTACK_API_KEY"
ADZUNA_APP_ID="YOUR_ADZUNA_APP_ID"
ADZUNA_APP_KEY="YOUR_ADZUNA_APP_KEY"
JOOBLE_API_KEY="YOUR_JOOBLE_API_KEY"

REDIS_URL="rediss://default:YOUR_REDIS_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT"
MARKET_CACHE_TTL_SECONDS="21600"
```

Example update command:

```bash
gcloud run services update ai-resume-parser \
  --region us-central1 \
  --set-env-vars DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DBNAME" \
  --set-env-vars JWT_SECRET="YOUR_LONG_RANDOM_SECRET" \
  --set-env-vars FRONTEND_ORIGINS="https://YOUR_VERCEL_DOMAIN" \
  --set-env-vars LLM_API_BASE="https://api.groq.com/openai/v1",LLM_MODEL="YOUR_GROQ_MODEL" \
  --set-env-vars THEIRSTACK_API_KEY="YOUR_THEIRSTACK_API_KEY" \
  --set-env-vars REDIS_URL="rediss://default:YOUR_REDIS_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT"
```

Source deploy from `backend/`:

```bash
cd backend
gcloud run deploy ai-resume-parser \
  --source . \
  --region us-central1 \
  --allow-unauthenticated
```

After deployment, your backend API base will look like:

```text
https://YOUR_CLOUD_RUN_SERVICE_URL/api
```

### Frontend On Vercel

Set these environment variables in Vercel:

```bash
NEXT_PUBLIC_API_BASE_URL=https://YOUR_CLOUD_RUN_SERVICE_URL/api
BACKEND_URL=https://YOUR_CLOUD_RUN_SERVICE_URL
```

`NEXT_PUBLIC_API_BASE_URL` is the main value used by the frontend fetch helpers. `BACKEND_URL` supports the Next.js rewrite fallback for `/api/*`.

After changing Vercel environment variables, redeploy the frontend.

### Database On Render

Use the external PostgreSQL connection string from Render and set it as `DATABASE_URL` in Cloud Run.

Use the format expected by SQLAlchemy:

```text
postgresql://USER:PASSWORD@HOST:PORT/DBNAME
```

If your provider gives a `postgres://` URL, convert it to `postgresql://`.

### Redis

Redis is optional but recommended for market analysis caching.

Set `REDIS_URL` in Cloud Run:

```text
rediss://default:YOUR_REDIS_PASSWORD@YOUR_REDIS_HOST:YOUR_REDIS_PORT
```

If `REDIS_URL` is missing or Redis is unavailable, market analysis still works without cache.

---

## CI And Verification

GitHub Actions are configured in `.github/workflows/ci.yml`.

CI runs:

- Backend dependency installation
- Backend compile check
- Pytest
- Frontend dependency installation
- Next.js production build

Run checks locally:

```bash
# Backend
PYTHONPATH=backend pytest -q

# Frontend
cd frontend
npm install
npm run build
```

---

## Common Workflows

### Parse A Resume

1. Register or log in.
2. Open `/resume`.
3. Upload a PDF or DOCX resume.
4. Parsed skills and experience are saved for that user.

### Run A Job Match

1. Open `/jobs`.
2. Select a parsed resume.
3. Paste a job title, company, and job description.
4. Run the match.
5. Review score, gaps, dimension scores, fit summary, and tips.
6. Use Ask AI for grounded follow-up questions.

### Generate Learning Recommendations

1. Run at least one job match.
2. Open `/learning`.
3. Select a match from history.
4. Generate a match-specific learning strategy.

### Analyze Market Trends

1. Configure at least one market provider API key.
2. Open `/market`.
3. Enter target role, location, country, experience level, and resume.
4. Run market analysis.
5. Review demanded skills, resume gaps, priorities, projects, and sample jobs.

---

## Troubleshooting

### Market analysis returns no data

Check Cloud Run logs and provider configuration.

```bash
gcloud run services logs read ai-resume-parser --region us-central1 --limit 200
```

Common causes:

- No provider API key is set.
- Provider key is invalid or out of credits.
- Provider returns no jobs for the role/location/date range.
- Country code is invalid for the provider.
- Redis URL is malformed if cache warnings appear.

### TheirStack returns 403

Check:

- `THEIRSTACK_API_KEY` is set in Cloud Run.
- A new Cloud Run revision was deployed after setting the key.
- The key has active credits and permission for the jobs search endpoint.
- The backend is reading the expected service revision.

### Frontend cannot reach backend

Check:

- `NEXT_PUBLIC_API_BASE_URL` in Vercel includes `/api`.
- `BACKEND_URL` in Vercel does not include `/api` if using rewrites.
- Cloud Run allows unauthenticated requests.
- `FRONTEND_ORIGINS` includes the Vercel domain or is set to `*`.

### Login works locally but not in production

Check:

- Same production backend is used by the frontend.
- `JWT_SECRET` is stable across Cloud Run revisions.
- Browser localStorage has `access_token`.
- HTTPS domain is correct in Vercel env vars.

### Resume parsing quality is weak

Install the spaCy model:

```bash
python -m spacy download en_core_web_sm
```

The app can run without it, but NER-based contact extraction may be weaker.

---

## Git Commands

Commit and push current work:

```bash
git status
git add .
git commit -m "Update project documentation"
git push origin main
```

For a frontend-only UI commit:

```bash
git add .gitignore frontend/app frontend/components
git commit -m "Redesign frontend with premium product UI"
git push origin main
```

---

## Notes

- Do not commit `.env`, `.env.local`, API keys, database passwords, Redis passwords, or provider tokens.
- Stateless Ask AI does not store chat history server-side.
- Market analyses are not stored in the database.
- Redis is used only as an optional cache for provider responses.
- The backend currently creates tables automatically at startup. Add migrations before large production schema changes.
