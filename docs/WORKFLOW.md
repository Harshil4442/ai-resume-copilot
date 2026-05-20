# AI Resume CoPilot Workflow

This document explains the complete product and technical workflow for AI Resume CoPilot. It is written as a GitHub-friendly Mermaid diagram document, so the diagrams render automatically on GitHub and in most Markdown preview tools.

For a visual browser version, open:

```text
docs/workflow.html
```

---

## 1. End-To-End Product Workflow

```mermaid
flowchart TD
    Start([User opens AI Resume CoPilot])
    Auth{Logged in?}
    Register[Register account]
    Login[Login]
    Token[JWT token stored in browser]
    Profile[Complete optional profile]
    ResumeUpload[Upload PDF or DOCX resume]
    ResumeParse[Parse resume into structured data]
    ResumeStore[(Store resume in PostgreSQL)]
    Dashboard[Dashboard overview]
    JobMatch[Run job match with selected resume and JD]
    MatchStore[(Store job match history)]
    AskAI[Ask AI about a specific match]
    Learning[Generate match-specific learning plan]
    Market[Analyze live job market trends]
    Actions[Use gaps, projects, bullets, and insights]

    Start --> Auth
    Auth -- No --> Register --> Login --> Token
    Auth -- Yes --> Token
    Token --> Profile
    Token --> Dashboard
    Profile --> ResumeUpload
    ResumeUpload --> ResumeParse --> ResumeStore
    ResumeStore --> Dashboard
    ResumeStore --> JobMatch
    JobMatch --> MatchStore --> Dashboard
    MatchStore --> AskAI
    MatchStore --> Learning
    ResumeStore --> Market
    Market --> Dashboard
    AskAI --> Actions
    Learning --> Actions
    Market --> Actions
```

### What This Workflow Does

1. **Authentication:** The user registers or logs in. The frontend stores the JWT token and sends it with protected API calls.
2. **Profile Setup:** The user can optionally add profile context such as target role, location, links, skills, education, and career preferences.
3. **Resume Parsing:** The user uploads a PDF or DOCX resume. The backend extracts text, sections, contact info, skills, and estimated experience.
4. **Job Matching:** The user selects a parsed resume and pastes a job description. The backend creates a detailed match analysis and stores the result.
5. **Ask AI:** The user can ask grounded questions about a specific resume and job match. The app does not store chat history.
6. **Learning Strategy:** The user selects a match from history and gets a gap-focused learning plan with projects, resume bullets, and interview talking points.
7. **Market Trends:** The user searches live job market data for a target role and compares repeated market skills with their resume.
8. **Dashboard:** The dashboard summarizes profile health, resume quality, match performance, activity, and recurring gaps.

---

## 2. System Architecture Workflow

```mermaid
flowchart LR
    subgraph Client["Frontend - Vercel"]
        Home[Premium landing page]
        AuthUI[Login and register]
        ProfileUI[Profile page]
        ResumeUI[Resume upload]
        JobsUI[Job match and Ask AI]
        LearningUI[Learning strategy]
        MarketUI[Market trends]
        DashboardUI[Dashboard analytics]
    end

    subgraph API["Backend - FastAPI on Google Cloud Run"]
        AuthAPI[/Auth router/]
        ResumeAPI[/Resume router/]
        JobsAPI[/Jobs router/]
        RagAPI[/RAG router/]
        RecAPI[/Recommendations router/]
        MarketAPI[/Market router/]
        AnalyticsAPI[/Analytics router/]
        LlmAPI[/LLM helper router/]
    end

    subgraph Services["Backend Services"]
        Parsing[Resume parsing service]
        Matching[Job matching service]
        Rag[Stateless RAG service]
        Recommender[Learning recommender]
        MarketAnalyzer[Market analyzer]
        LlmClient[Groq or OpenAI-compatible LLM client]
    end

    subgraph Data["Data and External Systems"]
        Postgres[(PostgreSQL on Render)]
        Redis[(Optional Redis cache)]
        Groq[Groq or OpenAI-compatible LLM]
        TheirStack[TheirStack jobs API]
        Adzuna[Adzuna jobs API]
        Jooble[Jooble jobs API]
    end

    Home --> AuthUI
    AuthUI --> AuthAPI
    ProfileUI --> AuthAPI
    ResumeUI --> ResumeAPI
    JobsUI --> JobsAPI
    JobsUI --> RagAPI
    LearningUI --> RecAPI
    MarketUI --> MarketAPI
    DashboardUI --> AnalyticsAPI

    AuthAPI --> Postgres
    ResumeAPI --> Parsing --> Postgres
    JobsAPI --> Matching --> Postgres
    RagAPI --> Rag --> Postgres
    RecAPI --> Recommender --> Postgres
    MarketAPI --> MarketAnalyzer
    AnalyticsAPI --> Postgres
    LlmAPI --> LlmClient

    Matching --> LlmClient
    Rag --> LlmClient
    Recommender --> LlmClient
    LlmClient --> Groq

    MarketAnalyzer --> Redis
    MarketAnalyzer --> TheirStack
    MarketAnalyzer --> Adzuna
    MarketAnalyzer --> Jooble
    MarketAnalyzer --> Postgres
```

### Architecture Responsibilities

| Layer | Responsibility |
| --- | --- |
| Frontend | Product UI, auth state, form flows, dashboards, charts, and API calls. |
| FastAPI routers | API boundary for auth, profile, resume, jobs, RAG, recommendations, market, LLM helpers, and analytics. |
| Services | Business logic for parsing, matching, RAG retrieval, learning plans, and market analysis. |
| PostgreSQL | Persistent user, profile, resume, job match, and skill coverage data. |
| Redis | Optional cache for repeated market provider responses. |
| LLM | Generates fit summaries, learning plans, grounded answers, bullets, and interview questions. |
| Job providers | Live job-market source data for trend analysis. |

---

## 3. Resume Parsing Workflow

```mermaid
flowchart TD
    Upload[User uploads resume]
    FileType{File type}
    PDF[PDF parser using pdfplumber]
    DOCX[DOCX parser using python-docx]
    Text[Extract raw text]
    Sections[Detect resume sections]
    Contact[Extract contact info]
    Skills[Extract skills with LLM and heuristics]
    Experience[Estimate experience years]
    Save[(Save Resume record)]
    Response[Return resume id, skills, sections, contact info]

    Upload --> FileType
    FileType -- PDF --> PDF --> Text
    FileType -- DOCX --> DOCX --> Text
    Text --> Sections
    Text --> Contact
    Text --> Skills
    Text --> Experience
    Sections --> Save
    Contact --> Save
    Skills --> Save
    Experience --> Save
    Save --> Response
```

### Resume Parsing Functionality

- Accepts PDF and DOCX uploads.
- Extracts raw text from the file.
- Detects sections such as experience, projects, education, skills, certifications, and other resume blocks.
- Extracts contact fields such as name, email, phone, LinkedIn, and GitHub.
- Extracts skills using LLM support when available, with heuristic fallback.
- Estimates years of experience from date ranges.
- Stores parsed resume data against the logged-in user.

---

## 4. Job Match Intelligence Workflow

```mermaid
flowchart TD
    SelectResume[Select parsed resume]
    PasteJD[Paste job title, company, and job description]
    Validate[Validate current user owns resume]
    JDExtract[Extract required skills from JD]
    EvidenceMap[Build skill confidence map from resume sections]
    Coverage[Classify full matches, partial matches, and true gaps]
    Score[Calculate weighted match score]
    Dimensions[Generate recruiter-style dimension scores]
    Summary[Generate fit summary and improvement tips]
    SaveMatch[(Save JobMatch)]
    MatchResult[Return match report]

    SelectResume --> Validate
    PasteJD --> JDExtract
    Validate --> EvidenceMap
    JDExtract --> Coverage
    EvidenceMap --> Coverage
    Coverage --> Score
    Coverage --> Dimensions
    Score --> Summary
    Dimensions --> Summary
    Summary --> SaveMatch
    SaveMatch --> MatchResult
```

### Job Match Functionality

- Verifies that the selected resume belongs to the current user.
- Extracts required and preferred skills from the job description.
- Compares job requirements against resume skills and resume evidence.
- Separates skills into:
  - full matches
  - partial matches
  - true gaps
- Produces:
  - match score
  - grade
  - skill verification rate
  - recruiter-style dimension scores
  - fit summary
  - improvement tips
- Persists the match so it can be used by the dashboard, Ask AI, and learning recommendations.

---

## 5. Stateless Ask AI Workflow

```mermaid
flowchart TD
    Question[User asks question about match]
    Ownership[Validate resume and match ownership]
    Load[Load Resume and JobMatch from PostgreSQL]
    Chunks[Build temporary evidence chunks]
    Intent[Classify question intent]
    Rank[Rank chunks with TF-IDF, source priority, and rule boosts]
    TopK[Select top context chunks]
    Prompt[Build strict grounded prompt]
    LLM[Call Groq or OpenAI-compatible LLM]
    Parse[Parse JSON answer with fallback handling]
    UI[Return answer, confidence, and follow-ups]

    Question --> Ownership --> Load --> Chunks
    Chunks --> Intent --> Rank --> TopK --> Prompt --> LLM --> Parse --> UI
```

### Ask AI Functionality

- Works for a specific resume and job match.
- Builds temporary context from up to 15 evidence sources:
  - resume summary
  - resume skills
  - experience
  - projects
  - education
  - other resume sections
  - job description
  - required skills
  - full matches
  - partial matches
  - true gaps
  - match score
  - dimension scores
  - fit summary
  - improvement tips
- Classifies questions into 7 intent groups:
  - missing skills
  - score explanation
  - evidence or proof
  - interview prep
  - learning path
  - resume improvement
  - general
- Retrieves top context chunks with TF-IDF similarity plus rule-based boosts.
- Sends only selected context to the LLM.
- Returns JSON containing:
  - answer
  - confidence
  - suggested follow-up questions
- Does not store:
  - chat history
  - chunks
  - embeddings
  - vector indexes

---

## 6. Learning Recommendation Workflow

```mermaid
flowchart TD
    SelectMatch[Select job match from history]
    LoadMatch[Load JobMatch and Resume]
    Inputs[Collect true gaps, partial matches, required skills, score, tips]
    LLMPlan[Try LLM hiring-manager strategy]
    Fallback[Fallback deterministic strategy]
    Normalize[Normalize strategy fields]
    Resources[Attach curated learning resources]
    Response[Return learning strategy]

    SelectMatch --> LoadMatch --> Inputs
    Inputs --> LLMPlan
    LLMPlan -- success --> Normalize
    LLMPlan -- unavailable --> Fallback --> Normalize
    Normalize --> Resources --> Response
```

### Learning Functionality

- Uses a saved job match as the source of truth.
- Generates recommendations for the exact job the user analyzed.
- Produces:
  - readiness summary
  - missing hiring signals
  - learning priorities
  - project recommendations
  - implementation steps
  - resume bullets
  - interview talking points
  - timeline
- Adds curated learning resources where available.
- Falls back to deterministic strategy generation if the LLM fails.

---

## 7. Market Skill Trend Analyzer Workflow

```mermaid
flowchart TD
    Query[User enters target role, location, country, experience, remote preference]
    ResumeChoice[Optional resume selection]
    ProviderRegistry[Find configured providers]
    CacheCheck[Check Redis cache]
    Providers[Search TheirStack, Adzuna, Jooble]
    Deduplicate[Deduplicate job postings]
    Extract[Extract skills once per job]
    Normalize[Normalize aliases and taxonomy]
    Count[Count skill frequency and percentage]
    Compare[Compare skills against resume evidence]
    Prioritize[Prioritize gaps by demand and resume status]
    Projects[Recommend projects and learning priorities]
    Result[Return trend report and sample jobs]

    Query --> ProviderRegistry
    ResumeChoice --> Compare
    ProviderRegistry --> CacheCheck
    CacheCheck -- cache hit --> Deduplicate
    CacheCheck -- cache miss --> Providers --> Deduplicate
    Deduplicate --> Extract --> Normalize --> Count --> Compare --> Prioritize --> Projects --> Result
```

### Market Analyzer Functionality

- Searches live job postings from configured providers.
- Supports fallback across 3 providers:
  - TheirStack
  - Adzuna
  - Jooble
- Uses Redis cache when available.
- Deduplicates jobs before analysis.
- Extracts skills from each job description.
- Counts a skill at most once per job.
- Uses a taxonomy with:
  - 14 skill categories
  - 132 canonical skills
  - 40 aliases
- Calculates skill demand percentages.
- Compares market skills with the user's resume and marks each as:
  - proven
  - claimed
  - missing
- Prioritizes gaps as:
  - critical
  - high
  - medium
  - low
- Generates project ideas and resume bullets to close high-value gaps.

---

## 8. Dashboard Analytics Workflow

```mermaid
flowchart TD
    Dashboard[User opens dashboard]
    LoadUser[Load user data]
    Resumes[Load resumes]
    Matches[Load job matches]
    Profile[Load profile]
    ProfileHealth[Calculate profile health]
    ResumeQuality[Calculate resume quality]
    MatchStats[Calculate match performance]
    Gaps[Find recurring gaps]
    Activity[Find latest activity]
    UI[Render dashboard cards and chart]

    Dashboard --> LoadUser
    LoadUser --> Resumes
    LoadUser --> Matches
    LoadUser --> Profile
    Resumes --> ResumeQuality
    Profile --> ProfileHealth
    Matches --> MatchStats
    Matches --> Gaps
    Resumes --> Activity
    Matches --> Activity
    ProfileHealth --> UI
    ResumeQuality --> UI
    MatchStats --> UI
    Gaps --> UI
    Activity --> UI
```

### Dashboard Functionality

- Shows profile completeness.
- Shows average match score.
- Shows resume count and match count.
- Shows last activity.
- Summarizes resume quality:
  - unique skills
  - evidenced skills
  - claimed-only skills
  - verification rate
  - quantified achievements
  - missing sections
- Summarizes match performance:
  - best match
  - weakest match
  - latest match
  - score trend chart
- Shows recurring skill gaps.
- Provides quick actions to update profile, upload resume, run match, and generate learning strategy.

---

## 9. Deployment Workflow

```mermaid
flowchart LR
    Dev[Developer pushes to GitHub]
    CI[GitHub Actions CI]
    BackendTests[Backend compile and pytest]
    FrontendBuild[Next.js production build]
    CloudBuild[Google Cloud Build]
    Artifact[Artifact Registry image]
    CloudRun[Cloud Run backend]
    Vercel[Vercel frontend]
    Render[(Render PostgreSQL)]
    Redis[(Redis cache)]
    External[External APIs and LLM]

    Dev --> CI
    CI --> BackendTests
    CI --> FrontendBuild
    Dev --> CloudBuild --> Artifact --> CloudRun
    Dev --> Vercel
    CloudRun --> Render
    CloudRun --> Redis
    CloudRun --> External
    Vercel --> CloudRun
```

### Deployment Functionality

- GitHub Actions verifies backend and frontend changes.
- Cloud Build can build and deploy the backend container.
- Cloud Run hosts the FastAPI backend.
- Vercel hosts the Next.js frontend.
- Render PostgreSQL stores application data.
- Redis is optional and used for provider response caching.
- Groq and job-provider APIs are called from the backend.

---

## 10. Feature-To-Route Map

| Feature | Frontend Page | Backend Route | Core Service |
| --- | --- | --- | --- |
| Register/Login | `/register`, `/login` | `/api/auth/register`, `/api/auth/login` | `security.py` |
| Profile | `/profile` | `/api/auth/profile` | auth router |
| Resume upload | `/resume` | `/api/resume/parse` | `services/parsing.py` |
| Resume list | `/resume`, `/jobs`, `/market` | `/api/resume/list` | resume router |
| Job match | `/jobs` | `/api/jobs/match` | `services/matching.py` |
| Match history | `/jobs`, `/learning` | `/api/jobs/matches` | jobs router |
| Ask AI | `/jobs` | `/api/rag/ask` | `services/rag/*` |
| Learning strategy | `/learning` | `/api/recommendations/match_strategy` | `services/recommender.py`, `llm_client.py` |
| Market trends | `/market` | `/api/market/analyze` | `services/market/*` |
| Dashboard | `/dashboard` | `/api/analytics/summary` | analytics router |
| Bullet rewrite | API utility | `/api/llm/rewrite_bullets` | `services/llm_client.py` |
| Interview questions | API utility | `/api/llm/interview_questions` | `services/llm_client.py` |

---

## 11. Quantifiable System Summary

| Metric | Current Value |
| --- | ---: |
| Backend API endpoints | 16 |
| Frontend routes/pages | 10 |
| SQLAlchemy database models | 5 |
| Pydantic schemas | 38 |
| Backend Python source files | 38 |
| Frontend TypeScript/TSX files | 17 |
| Market providers | 3 |
| Market skill categories | 14 |
| Canonical market skills | 132 |
| Skill aliases | 40 |
| Ask AI evidence chunk sources | 15 |
| Ask AI intent categories | 7 |
| Project recommendation templates | 5 |
| Deployment/CI config files | 6 |

---

## 12. High-Level Value

AI Resume CoPilot turns a resume into a connected career intelligence workflow:

1. **Understand the candidate:** profile and resume parsing.
2. **Understand the job:** skill extraction and match scoring.
3. **Explain the gap:** full/partial/gap classification and Ask AI.
4. **Improve the candidate:** learning plans and project recommendations.
5. **Track the market:** live job trend analysis.
6. **Track progress:** dashboard analytics and match history.

